"""Optional visible, persistent Playwright adapter for generic application forms.

It never solves CAPTCHAs, enters passwords, guesses fields, or retries a final
submission. Login is completed manually in the persistent local browser profile.
"""

import mimetypes
from pathlib import Path
from collections.abc import Callable
import re
from typing import Any
from urllib.parse import urljoin

from sampoagent.agents.browser import FormInspection, SubmissionResult
from sampoagent.agents.forms import FormSchema, parse_file_size_limit
from sampoagent.agents.pinned_proxy import PinnedHttpsProxy
from sampoagent.applications.field_resolver import FormField
from sampoagent.applications.urls import is_safe_public_https_url, is_same_public_origin, looks_like_authentication_page, looks_like_captcha_page


_SUBMIT_TEXT = re.compile(r"\b(apply|submit|send application|send my application|lähetä hakemus|jätä hakemus|hae paikkaa)\b", re.IGNORECASE)
_NEXT_STEP_TEXT = re.compile(r"\b(next|continue|save and continue|jatka|seuraava|nästa|fortsätt|gå vidare)\b", re.IGNORECASE)
_CAPTCHA_SELECTOR = "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], [data-sitekey], .g-recaptcha, .h-captcha, input[name*='captcha' i]"
_CONFIRMATION_MARKERS = ("application received", "application submitted", "thank you for applying", "thank you for your application", "hakemuksesi on vastaanotettu", "kiitos hakemuksestasi", "hakemus lähetetty")


def detect_page_state(*, url: str, text: str, password_field_count: int, challenge_element_found: bool) -> tuple[bool, bool]:
    """Pure detector exported for deterministic tests and alternative adapters."""
    return (
        looks_like_captcha_page(url, text, challenge_element_found),
        looks_like_authentication_page(url, password_field_count),
    )


class BrowserUnavailable(RuntimeError):
    """The optional Playwright package or its Chromium runtime is missing."""


class PlaywrightBrowserAgent:
    """A user-visible browser with an isolated, persistent SampoAgent profile."""

    configured = True

    def __init__(self, profile_dir: Path, *, restrict_cross_origin: bool = True) -> None:
        self.profile_dir = profile_dir
        self._restrict_cross_origin = restrict_cross_origin
        self._allowed_origin: str | None = None
        self._egress_proxy: PinnedHttpsProxy | None = None
        self._playwright: Any = None
        self._context: Any = None
        self._closing = False
        self._page: Any = None
        self._fields: dict[str, Any] = {}
        self._kinds: dict[str, str] = {}
        self._radio_options: dict[str, tuple[str, ...]] = {}
        self._file_constraints: dict[str, tuple[tuple[str, ...], int | None]] = {}
        self._multiple_file_fields: set[str] = set()
        self._submit_buttons: list[Any] = []
        self._next_buttons: list[Any] = []
        self._expected_step_values: dict[str, str] = {}

    def start(self) -> None:
        if self._context is not None:
            return
        if self._playwright is not None:
            previous_playwright = self._playwright
            self._playwright = None
            previous_playwright.stop()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserUnavailable("Install the optional browser extra, then run `playwright install chromium`.") from exc
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._egress_proxy = PinnedHttpsProxy(
                self._allowed_origin if self._restrict_cross_origin else None
            )
            self._egress_proxy.start()
            self._playwright = sync_playwright().start()
            self._context = self._playwright.chromium.launch_persistent_context(
                str(self.profile_dir), headless=False, accept_downloads=False, service_workers="block",
                proxy=self._egress_proxy.playwright_proxy,
            )
            self._context.route("**/*", self._guard_request)
            self._context.route_web_socket("**/*", self._block_websocket)
            self._context.on("close", self._on_context_closed)
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        except Exception as exc:
            self.close()
            raise BrowserUnavailable("Chromium could not start. Install the Playwright browser runtime and try again.") from exc

    def close(self) -> None:
        self._closing = True
        context = self._context
        self._context = None
        self._page = None
        try:
            try:
                if context is not None:
                    context.close()
            finally:
                proxy = self._egress_proxy
                self._egress_proxy = None
                try:
                    if proxy is not None:
                        proxy.close()
                finally:
                    playwright = self._playwright
                    self._playwright = None
                    try:
                        if playwright is not None:
                            playwright.stop()
                    finally:
                        self._allowed_origin = None
        finally:
            self._closing = False

    def _on_context_closed(self, *_args: object) -> None:
        """Stop network egress if the user closes Chromium outside this adapter."""
        if self._closing:
            return
        self._context = None
        self._page = None
        self._allowed_origin = None
        proxy = self._egress_proxy
        self._egress_proxy = None
        if proxy is not None:
            proxy.close()

    def _require_page(self) -> Any:
        if self._page is None:
            self.start()
        return self._page

    def _guard_request(self, route: Any) -> None:
        """Block non-HTTPS and non-public browser requests before dispatch.

        Application mode is restricted to the inspected job origin to prevent
        candidate data from being posted to third-party analytics endpoints.
        Manual login can opt out of same-origin restriction, but still gets
        the public HTTPS check and the proxy's pinned-IP policy.
        """
        request_url = route.request.url
        if request_url.startswith(("data:", "blob:")) or request_url == "about:blank":
            route.continue_()
            return
        if self._restrict_cross_origin and (
            self._allowed_origin is None or not is_same_public_origin(self._allowed_origin, request_url)
        ):
            route.abort("blockedbyclient")
            return
        if is_safe_public_https_url(request_url):
            route.continue_()
            return
        route.abort("blockedbyclient")

    @staticmethod
    def _block_websocket(route: Any) -> None:
        """Do not let pages open an uninspected WebSocket egress path."""
        route.close(code=1008, reason="WebSocket connections are not supported by the safe application adapter")

    def open(self, url: str) -> None:
        if not is_safe_public_https_url(url):
            raise ValueError("Only public HTTPS application pages without embedded credentials are supported")
        if self._restrict_cross_origin:
            self._allowed_origin = url
        self._expected_step_values = {}
        page = self._require_page()
        if self._egress_proxy is not None:
            self._egress_proxy.set_allowed_origin(self._allowed_origin if self._restrict_cross_origin else None)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if not is_safe_public_https_url(page.url):
            raise ValueError("The application page redirected to an unsafe destination")

    def _page_text(self) -> str:
        try:
            return self._require_page().locator("body").inner_text(timeout=2500)[:50000]
        except Exception:
            return ""

    def inspect_form(self) -> FormInspection:
        page = self._require_page()
        text = self._page_text()
        controls = page.locator("input, textarea, select")
        fields: list[FormField] = []
        self._fields = {}
        self._kinds = {}
        self._radio_options = {}
        self._file_constraints = {}
        self._multiple_file_fields = set()
        current_values: dict[str, str] = {}
        uploaded_files: dict[str, dict[str, str]] = {}
        count = min(controls.count(), 150)
        password_count = page.locator("input[type='password']").count()
        form_count = page.locator("form, [role='form']").count()
        raw_controls: list[dict[str, Any]] = []
        for index in range(count):
            locator = controls.nth(index)
            try:
                if not locator.is_visible() or not locator.is_enabled():
                    continue
                tag = locator.evaluate("el => el.tagName.toLowerCase()")
                input_type = (locator.get_attribute("type") or "text").casefold() if tag == "input" else tag
                if input_type in {"hidden", "password", "submit", "button", "reset", "image", "search"}:
                    continue
                name = locator.get_attribute("name") or ""
                element_id = locator.get_attribute("id") or ""
                metadata = locator.evaluate("""el => {
                  const text = node => (node?.innerText || node?.textContent || '').trim();
                  const labels = Array.from(el.labels || []).map(text).filter(Boolean);
                  const labelledBy = (el.getAttribute('aria-labelledby') || '').split(/\\s+/).filter(Boolean)
                    .map(id => text(document.getElementById(id))).filter(Boolean);
                  const describedBy = (el.getAttribute('aria-describedby') || '').split(/\\s+/).filter(Boolean)
                    .map(id => text(document.getElementById(id))).filter(Boolean);
                  const fieldset = el.closest('fieldset');
                  const legend = text(fieldset?.querySelector('legend'));
                  const form = el.closest('form,[role="form"]');
                  return {
                    labels, labelledBy, describedBy, legend,
                    ariaLabel: el.getAttribute('aria-label') || '',
                    placeholder: el.getAttribute('placeholder') || '',
                    autocomplete: el.getAttribute('autocomplete') || '',
                    accept: el.getAttribute('accept') || '',
                    allowsMultipleFiles: el.type === 'file' && el.multiple,
                    constraints: Object.fromEntries(['min', 'max', 'step', 'pattern', 'minlength', 'maxlength']
                      .map(name => [name, el.getAttribute(name)]).filter(([, value]) => value !== null)
                      .concat(!el.hasAttribute('min') && el.hasAttribute('value') ? [['step_base', el.getAttribute('value')]] : [])),
                    maxFileSize: el.getAttribute('data-max-file-size') || el.getAttribute('data-max-size') || '',
                    name: el.getAttribute('name') || '',
                    value: el.value || '',
                    formIndex: form ? Array.from(document.querySelectorAll('form,[role="form"]')).indexOf(form) : -1,
                  };
                }""")
                if not isinstance(metadata, dict):
                    continue
                label_value = " ".join(str(value).strip() for value in metadata.get("labels", []) if str(value).strip())
                labelled_by = " ".join(str(value).strip() for value in metadata.get("labelledBy", []) if str(value).strip())
                if label_value:
                    accessible_name, label_source = label_value, "label"
                elif labelled_by:
                    accessible_name, label_source = labelled_by, "aria-labelledby"
                elif metadata.get("ariaLabel"):
                    accessible_name, label_source = str(metadata["ariaLabel"]).strip(), "aria-label"
                elif metadata.get("placeholder"):
                    accessible_name, label_source = str(metadata["placeholder"]).strip(), "placeholder"
                elif name:
                    accessible_name, label_source = name, "name"
                else:
                    accessible_name, label_source = "", "unlabelled"
                required = locator.get_attribute("required") is not None or locator.get_attribute("aria-required") == "true"
                max_file_size_raw = str(metadata.get("maxFileSize") or "")
                constraints_data = metadata.get("constraints", {})
                constraints = tuple(
                    (key, str(constraints_data[key]))
                    for key in ("min", "max", "step", "pattern", "minlength", "maxlength", "step_base")
                    if isinstance(constraints_data, dict) and key in constraints_data
                )
                max_file_size = parse_file_size_limit(max_file_size_raw)
                if max_file_size is None and re.fullmatch(r"\d+", max_file_size_raw):
                    max_file_size = int(max_file_size_raw)
                if max_file_size is None:
                    max_file_size = parse_file_size_limit(" ".join(str(value) for value in metadata.get("describedBy", [])))
                if input_type == "file":
                    kind = "file"
                elif input_type in {"email", "tel", "date", "number", "checkbox", "radio"}:
                    kind = input_type
                elif tag == "select":
                    kind = "select"
                elif tag == "textarea":
                    kind = "textarea"
                else:
                    kind = "text"
                raw_controls.append({
                    "index": index, "locator": locator, "tag": tag, "kind": kind,
                    "name": name, "element_id": element_id, "required": required,
                    "autocomplete": str(metadata.get("autocomplete") or ""),
                    "accepted_types": tuple(value.strip().casefold() for value in str(metadata.get("accept") or "").split(",") if value.strip()),
                    "max_file_size_bytes": max_file_size,
                    "constraints": constraints,
                    "allows_multiple_files": bool(metadata.get("allowsMultipleFiles")),
                    "accessible_name": accessible_name, "label_source": label_source,
                    "description": " ".join(dict.fromkeys(str(value).strip() for value in metadata.get("describedBy", []) if str(value).strip())),
                    "legend": str(metadata.get("legend") or "").strip(),
                    "form_index": int(metadata.get("formIndex", -1)),
                    "value": str(metadata.get("value") or ""),
                })
            except Exception:
                # A stale or inaccessible control is not guessed; required fields
                # remain unresolved and the application stops for review.
                continue

        consumed_groups: set[tuple[int, str, str]] = set()
        used_ids: set[str] = set()
        for control in raw_controls:
            kind = str(control["kind"])
            name = str(control["name"])
            if kind in {"radio", "checkbox"} and name:
                group_key = (int(control["form_index"]), name, kind)
                if group_key in consumed_groups:
                    continue
                grouped = [
                    item for item in raw_controls
                    if (int(item["form_index"]), str(item["name"]), str(item["kind"])) == group_key
                ]
                consumed_groups.add(group_key)
            else:
                group_key = (int(control["form_index"]), str(control["element_id"]), kind)
                grouped = [control]

            group_legend = next((str(item["legend"]) for item in grouped if item["legend"]), "")
            if kind == "radio":
                field_kind = "radio"
                label = group_legend or str(control["accessible_name"])
                options = tuple(str(item["accessible_name"]) or str(item["value"]) for item in grouped)
                field_id = name or str(control["element_id"]) or f"unlabelled-{control['index']}"
                value = next((str(item["accessible_name"]) or str(item["value"]) for item in grouped if item["locator"].is_checked()), "")
                locator_value: Any = [item["locator"] for item in grouped]
                label_source = "fieldset legend" if group_legend else str(control["label_source"])
            elif kind == "checkbox" and len(grouped) > 1:
                field_kind = "checkbox_group"
                label = group_legend or str(control["accessible_name"])
                options = tuple(dict.fromkeys(str(item["accessible_name"]) or str(item["value"]) for item in grouped))
                field_id = name or str(control["element_id"]) or f"unlabelled-{control['index']}"
                value = ", ".join(str(item["accessible_name"]) or str(item["value"]) for item in grouped if item["locator"].is_checked())
                locator_value = [item["locator"] for item in grouped]
                label_source = "fieldset legend" if group_legend else str(control["label_source"])
            else:
                field_kind = kind
                label = str(control["accessible_name"])
                if group_legend and group_legend.casefold() != label.casefold():
                    label = f"{group_legend}: {label}" if label else group_legend
                options = tuple(value.strip() for value in control["locator"].locator("option").all_inner_texts() if value.strip()) if kind == "select" else ()
                field_id = name or str(control["element_id"]) or f"unlabelled-{control['index']}"
                locator_value = control["locator"]
                label_source = str(control["label_source"])
                if kind == "file":
                    value = ""
                    selected_file = control["locator"].evaluate("""async el => {
                      const file = el.files && el.files[0];
                      if (!file) return null;
                      const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
                      const hash = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).map(byte => byte.toString(16).padStart(2, '0')).join('');
                      return {name: file.name, sha256: hash};
                    }""")
                    if isinstance(selected_file, dict):
                        uploaded_files[field_id] = {"name": str(selected_file.get("name", "")), "sha256": str(selected_file.get("sha256", ""))}
                elif kind == "checkbox":
                    value = "yes" if control["locator"].is_checked() else "no"
                elif kind == "select":
                    selected = control["locator"].locator("option:checked")
                    value = selected.inner_text().strip() if selected.count() else ""
                else:
                    value = control["locator"].input_value()

            if field_id in used_ids:
                field_id = f"{field_id}#{control['index']}"
            used_ids.add(field_id)
            description = " ".join(dict.fromkeys(str(item["description"]) for item in grouped if item["description"]))
            field = FormField(
                field_id, label, any(bool(item["required"]) for item in grouped),
                kind=field_kind, options=options, autocomplete=str(control["autocomplete"]),
                name=name, description=description, source=label_source, group_label=group_legend,
                accepted_types=tuple(control["accepted_types"]),
                max_file_size_bytes=control["max_file_size_bytes"],
                allows_multiple_files=bool(control["allows_multiple_files"]),
                constraints=tuple(control["constraints"]),
            )
            fields.append(field)
            self._fields[field_id] = locator_value
            self._kinds[field_id] = field_kind
            if field_kind == "radio":
                self._radio_options[field_id] = options
            if field_kind == "file":
                self._file_constraints[field_id] = (field.accepted_types, field.max_file_size_bytes)
                if field.allows_multiple_files:
                    self._multiple_file_fields.add(field_id)
            current_values[field_id] = value

        buttons = page.locator("button, input[type='submit'], [role='button']")
        self._submit_buttons = []
        self._next_buttons = []
        has_next_step = False
        for index in range(min(buttons.count(), 50)):
            button = buttons.nth(index)
            try:
                name = (button.inner_text(timeout=500) or button.get_attribute("value") or button.get_attribute("aria-label") or "").strip()
                belongs_to_single_form = button.evaluate("""el => {
                  const contexts = Array.from(document.querySelectorAll('form,[role="form"]'));
                  if (contexts.length !== 1) return false;
                  const context = contexts[0];
                  return el.closest('form,[role="form"]') === context || el.form === context;
                }""")
                if belongs_to_single_form and button.is_visible() and button.is_enabled():
                    if _SUBMIT_TEXT.search(name):
                        self._submit_buttons.append(button)
                    elif _NEXT_STEP_TEXT.search(name):
                        has_next_step = True
                        self._next_buttons.append(button)
            except Exception:
                continue
        try:
            challenge_found = page.locator(_CAPTCHA_SELECTOR).count() > 0
        except Exception:
            challenge_found = False
        captcha, authentication = detect_page_state(
            url=page.url, text=text, password_field_count=password_count, challenge_element_found=challenge_found
        )
        try:
            checkpoint = page.locator("[aria-current='step'], [role='progressbar'], .step.active, [data-step-current='true']").first.inner_text(timeout=500)
        except Exception:
            checkpoint = ""
        try:
            action_url = (
                page.locator("form, [role='form']").first.evaluate(
                    "form => (form instanceof HTMLFormElement ? form.action : form.getAttribute('action')) || location.href"
                )
                if form_count == 1
                else ""
            )
        except Exception:
            action_url = ""
        if action_url:
            action_url = urljoin(page.url, str(action_url))
        schema = FormSchema.build(
            fields=tuple(fields), page_url=page.url, action_url=str(action_url),
            navigation_checkpoint=str(checkpoint).strip()[:240], has_next_step=has_next_step,
            single_form_context=form_count <= 1,
        )
        return FormInspection(
            tuple(fields), captcha, authentication, self.validation_errors(), schema.signature,
            page.url, len(self._submit_buttons) == 1, current_values, uploaded_files, schema,
            len(self._next_buttons) == 1,
        )

    def fill(self, field: str, value: str) -> None:
        locator = self._fields.get(field)
        if locator is None or not value.strip():
            raise ValueError("Form field was not detected or answer is empty")
        kind = self._kinds[field]
        validation_locator = locator
        if kind == "select":
            locator.select_option(label=value)
        elif kind == "radio":
            matching = [item for item, option in zip(locator, self._radio_options[field]) if option.casefold() == value.casefold()]
            if len(matching) != 1:
                raise ValueError("Radio answer did not match exactly one visible option")
            matching[0].check()
            validation_locator = matching[0]
        elif kind == "checkbox":
            if value.casefold() in {"yes", "true", "1", "checked"}:
                locator.check()
            elif value.casefold() in {"no", "false", "0", "unchecked"}:
                locator.uncheck()
            else:
                raise ValueError("Checkbox answer was not a confirmed yes/no value")
        elif kind in {"checkbox_group", "file"}:
            raise ValueError("This control needs a dedicated, explicitly matched field mapping")
        else:
            locator.fill(value)
        # Keep employer-supplied regex semantics in the browser rather than
        # evaluating potentially pathological patterns in Python's backtracking engine.
        if not validation_locator.evaluate("el => el.checkValidity()"):
            raise ValueError("The answer does not satisfy the employer form's current validation constraints")
        if not hasattr(self, "_expected_step_values"):
            self._expected_step_values = {}
        self._expected_step_values[field] = value

    def upload(self, field: str, path: str) -> None:
        locator = self._fields.get(field)
        if locator is None or self._kinds.get(field) != "file" or not Path(path).is_file():
            raise ValueError("The requested local CV upload field or file is unavailable")
        if field in self._multiple_file_fields:
            raise ValueError("This form accepts multiple files; prepare and review the complete attachment set manually")
        local_path = Path(path)
        accepted_types, max_file_size = self._file_constraints.get(field, ((), None))
        if max_file_size is not None and local_path.stat().st_size > max_file_size:
            raise ValueError("The CV exceeds the employer form's declared maximum file size")
        if accepted_types:
            suffix = local_path.suffix.casefold()
            mime = (mimetypes.guess_type(local_path.name)[0] or "").casefold()
            accepted = any(
                token == suffix or token == mime or (token.endswith("/*") and mime.startswith(token[:-1]))
                for token in accepted_types
            )
            if not accepted:
                raise ValueError("The CV file type is not accepted by the employer form")
        locator.set_input_files(path)

    def validation_errors(self) -> tuple[str, ...]:
        page = self._require_page()
        try:
            errors = page.locator("[aria-invalid='true'], :invalid").evaluate_all(
                "els => els.map(el => (el.validationMessage || el.getAttribute('aria-label') || el.name || 'Form field') + ' needs review').slice(0,20)"
            )
            return tuple(str(item)[:180] for item in errors if item)
        except Exception:
            return ()

    def advance_step(
        self,
        *,
        expected_signature: str,
        pre_click_check: Callable[[], bool] | None = None,
    ) -> FormInspection:
        """Advance one uniquely identified same-origin form page exactly once."""
        page = self._require_page()
        inspection = self.inspect_form()
        schema = inspection.schema
        if inspection.captcha_detected or inspection.authentication_required:
            raise RuntimeError("The current employer page needs manual review")
        if not is_safe_public_https_url(inspection.final_url or page.url):
            raise RuntimeError("The current employer page is not a safe HTTPS origin")
        if self._allowed_origin and not is_same_public_origin(self._allowed_origin, inspection.final_url or page.url):
            raise RuntimeError("The current employer page changed origin")
        if schema is None or not schema.single_form_context or not schema.has_next_step:
            raise RuntimeError("No supported next application page was detected")
        if schema.action_url and not is_same_public_origin(inspection.final_url or page.url, schema.action_url):
            raise RuntimeError("The next form action changed origin")
        if inspection.signature != expected_signature:
            raise RuntimeError("The application page changed before Continue")
        if len(self._next_buttons) != 1 or not inspection.next_step_control_ready:
            raise RuntimeError("A unique Continue control was not detected")
        mismatched_values = [
            field_id
            for field_id, expected in getattr(self, "_expected_step_values", {}).items()
            if inspection.values.get(field_id) != expected
        ]
        if mismatched_values:
            raise RuntimeError("A prepared application value changed before Continue")
        if pre_click_check is not None:
            try:
                if not pre_click_check():
                    raise RuntimeError("Application permission changed before Continue")
            except Exception:
                raise RuntimeError("Application permission changed before Continue") from None
        previous_url = inspection.final_url or page.url
        try:
            self._next_buttons[0].click(timeout=5000)
            page.wait_for_timeout(1000)
            advanced = self.inspect_form()
        except Exception:
            # A Next click may have saved the current page. Never repeat it
            # automatically if the resulting page cannot be inspected.
            raise RuntimeError("The application step transition could not be verified") from None
        if not is_same_public_origin(previous_url, advanced.final_url or page.url):
            raise RuntimeError("The application step changed origin")
        self._expected_step_values = {}
        return advanced

    def submit(
        self,
        url: str,
        *,
        expected_signature: str | None = None,
        expected_uploads: dict[str, dict[str, str]] | None = None,
        pre_click_check: Callable[[], bool] | None = None,
    ) -> SubmissionResult:
        page = self._require_page()
        inspection = self.inspect_form()
        if inspection.captcha_detected:
            return SubmissionResult(False, True, "CAPTCHA detected; manual action required", final_url=url, captcha_detected=True)
        if inspection.authentication_required:
            return SubmissionResult(False, True, "Employer sign-in is required", final_url=url)
        if not is_same_public_origin(url, inspection.final_url or page.url):
            return SubmissionResult(False, True, "The employer form redirected to another origin; no submit action was taken", final_url=inspection.final_url or page.url)
        if expected_signature and inspection.signature != expected_signature:
            return SubmissionResult(False, True, "The application form changed after review; no submit action was taken", final_url=inspection.final_url or page.url)
        mismatched_values = [
            field_id
            for field_id, expected in getattr(self, "_expected_step_values", {}).items()
            if inspection.values.get(field_id) != expected
        ]
        if mismatched_values:
            return SubmissionResult(False, True, "A prepared application value changed after review; no submit action was taken", final_url=inspection.final_url or page.url)
        if expected_uploads is not None:
            actual_uploads = {
                field_id: {"name": file.get("name", ""), "sha256": file.get("sha256", "")}
                for field_id, file in inspection.uploaded_files.items()
            }
            if actual_uploads != expected_uploads:
                return SubmissionResult(False, True, "The selected CV file changed after review; no submit action was taken", final_url=inspection.final_url or page.url)
        if not inspection.submit_control_ready:
            return SubmissionResult(False, True, "No unique final application button was detected", final_url=url)
        if pre_click_check is not None:
            try:
                if not pre_click_check():
                    return SubmissionResult(False, True, "Authorization or worker state changed before the final click; no submission was made", final_url=inspection.final_url or page.url)
            except Exception:
                return SubmissionResult(False, True, "The final authorization check failed; no submission was made", final_url=inspection.final_url or page.url)
        try:
            self._submit_buttons[0].click(timeout=5000)
            page.wait_for_timeout(1500)
            body = self._page_text().casefold()
            if looks_like_captcha_page(page.url, body, page.locator(_CAPTCHA_SELECTOR).count() > 0):
                return SubmissionResult(False, True, "An access challenge appeared after the submit click", final_url=url, captcha_detected=True)
            if not is_safe_public_https_url(page.url):
                return SubmissionResult(False, True, "The submission destination changed unexpectedly", final_url=url, outcome_unknown=True)
            if any(marker in body for marker in _CONFIRMATION_MARKERS):
                return SubmissionResult(True, False, "Employer confirmation detected on the page", final_url=page.url)
            return SubmissionResult(False, True, "Submit was clicked but employer confirmation was not detected", final_url=page.url, outcome_unknown=True)
        except Exception:
            # A click or navigation may have reached the employer. Never retry it.
            return SubmissionResult(False, True, "Submit outcome is uncertain; automatic retry is disabled", final_url=page.url, outcome_unknown=True)
