# SampoAgent tam otomatik başvuru — uygulama planı

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Yerel SampoAgent'ı kaynaklı aday verisi, doğrulanmış ilan, güvenli form adaptörleri ve geri alınabilir kullanıcı kapsamlı Autopilot ile uçtan uca başvuru aracına dönüştürmek.

**Architecture:** FastAPI/SQLite yerel kalır. Kaynak adaptörleri ilanları normalize eder; repository idempotent kuyruk ve olayları tutar; form adapter'ları yalnız form şeması üretip veriyi taşıyan mekanik rol oynar; tek politika servisi her dış işlemde karar verir. Mail gönderimi read-only OAuth'tan ayrılmış, kullanıcı ayrıca izin verdiğinde açılan outbox entegrasyonudur.

**Tech Stack:** Python 3.11+, FastAPI, SQLite, pytest, opsiyonel Playwright/Chromium; Gmail API ve Microsoft Graph yalnız ayrı delegated send scope onayı sonrası.

**Spec:** [2026-09-29-full-application-automation-design.md](../specs/2026-09-29-full-application-automation-design.md)

## Global Constraints

- Profil, CV, token, aday e-postası ve gerçek başvuru bilgisi repo fixture'larına veya loglara eklenmez.
- Boş, AI çıkarımlı, expired, conflicted veya declined cevap hiçbir zaman teyitli gerçek sayılmaz.
- Kullanıcı hedef rolü seçmeden sistem o rolü arama/başvuru kapsamına eklemez.
- Full Autopilot grant'i en az bir etkin kullanıcı-seçili occupation/career profile veya açık arama terimine bağlıdır; her queued job o scope'a tekrar eşleşmeli, kapsam değişikliği izni geçersiz kılmalıdır.
- Dry Run dış ağa aday verisi göndermez; High-risk beyanlar Autopilot'ta dahi manuel kalır.
- CAPTCHA, login/MFA ve erişim kontrolleri bypass edilmez; Submit timeout'u yeniden denenmez.
- Paket onayı ilan, domain, form imzası, cevap veya attachment değişince geçersizdir.
- Playwright'ın canlı file input'undaki upload adı/checksum kümesi, onaylı paketle final click öncesinde eşleşmelidir.
- SQLite migration'ı mevcut veriyi korumalıdır; public release test/fixture'larında sentetik veri kullanılır.

## Review Focus

- Aynı ilana eşzamanlı iki worker başvuruyu çoğaltmasın — Task 3'te unique-claim ve multi-worker testleri.
- Kullanıcı onayından sonra form/ilan/CV değişirse eski onay kullanılmasın — Task 1 ve Task 4'te hash-invalidation fixture'ı.
- Browser input file state'i runner'ın son paket kontrolünden sonra değişirse de adapter tıklamadan durmalı; uygulama gönderim-belirsiz kuyruğuna alınmamalı.
- CAPTCHA, MFA, yasal beyan veya unknown required field her modda dursun — Task 4/7'de FI/SV/EN test matrisleri.
- Submit sonrasında ağ kesintisi kör retry yaratmasın — Task 5'te crash/timeout reconciliation testi.
- Yanlış eşleşen işveren e-postası başvuruyu “alındı” yapmasın — Task 6'da sender/role/reference ambiguity testleri.

---

### Task 1: Başvuru paketi ve kesin onay sözleşmesini tamamla

**Files:**
- Modify: `sampoagent/applications/runner.py`
- Modify: `sampoagent/applications/workflow.py`
- Modify: `sampoagent/agents/browser.py`
- Modify: `sampoagent/agents/playwright_adapter.py`
- Modify: `sampoagent/db/repository.py`
- Modify: `sampoagent/app/main.py`
- Test: `tests/test_application_automation.py`
- Test: `tests/test_application_review_ui.py`
- Test: `tests/test_playwright_adapter.py`

**Interfaces:** `process_application(repository, application_id, browser) -> str`; `BrowserAgent.submit(url, *, expected_signature=None, expected_uploads=None) -> SubmissionResult`; `repository.approve_application_review(application_id, package_hash) -> bool`.

- [x] Test Smart Approval'ın exact package beklemesini, medium risk cevabını ancak inceleme sonrası göndermesini, hash değişince beklemesini ve cross-origin confirmation'ı başarı saymamasını yaz.
- [x] Başarısız testleri beklenen policy/eksik UI davranışıyla doğrula.
- [x] Paket görünümünü, tek kullanımlık hash onayını ve her son tıklama öncesi domain/form-imza kontrolünü uygula.
- [x] İlan doğrulamasını listing snapshot hash'ine bağla; değişen hedef ve açıklama için job UI'da yeniden doğrulama iste.
- [x] İlgili testleri çalıştır: `py -m pytest tests/test_application_automation.py tests/test_application_review_ui.py tests/test_playwright_adapter.py tests/test_automation_controls.py -q`.
- [x] Senaryo: CV baytları browser doldurma sırasında değişirse yeni hash oluştur, eski onayı iptal et ve Submit'e tıklama.
- [x] Senaryo: uygulama yeniden başlatılırken tam onaylı paket state'i kayıpsız yüklenir; stale form imzası Submit'ten önce yeniden incelenmek üzere sunulur.

### Task 2: Aday soru bankası, kaynak ve çelişki modelini tamamla

**Files:**
- Modify: `sampoagent/candidate/questions.py`
- Modify: `sampoagent/app/onboarding.py`
- Modify: `sampoagent/app/main.py`
- Modify: `sampoagent/applications/answers.py`
- Modify: `sampoagent/applications/field_resolver.py`
- Modify: `sampoagent/db/repository.py`
- Test: `tests/test_questionnaire.py`
- Test: `tests/test_application_fields.py`
- Test: `tests/test_answer_provenance.py`
- Test: `tests/test_application_automation.py`
- Test: `tests/test_application_review_ui.py`
- Test: `tests/test_automation_controls.py`

**Interfaces:** Soru kaydı stable ID, localized label, value type, sensitivity, scope ve expiry taşımalı; `resolve_application_fields()` yalnız source-confirmed alanları eşlemeli.

- [x] Anket soru meta verisini stable ID, scalar/repeatable/choice/date/amount/contact-link tipi, kategori, hassasiyet ve tekrar kullanım kapsamıyla tanımla.
- [x] Answer bank için geriye uyumlu SQLite migration ekle: source reference, explicit answer state, confirmation time, scope country/employer alanları ve optional validity. Kapsamı belirsiz eski eligibility cevabını otomatik kullanma.
- [x] Work-permission country scope, expiry, unknown, declined, conflict, application-specific ve legacy-answer senaryolarını sentetik testlerle kapsa.
- [x] Açıkça onaylanan skill/language/license/certificate liste cevaplarını source-linked candidate facts olarak materialize et; superseded cevapların ürettiği fact'leri deactivate et. Hobi/transferable skill CV'nin profesyonel skill listesine girmesin.
- [x] Salary, notice, availability, relocation, shift, language, licence, education, employment history, references, assessment, adjustment, demographic decline, privacy consent ve motivation eşlemelerini sentetik FI/SV/EN fixture'larıyla kapsa; hassas/ilan-özel sorular eşlenmeden manuel kalsın.
- [x] Güvenli stable sorular için employer-scoped answer creation/review/resolution UI'ını tamamla (şimdilik salary, notice, start date, shifts); sadece tam işveren kapsamı eşleşsin. İlan-özel motivation, assessment, adjustment, demographic ve privacy yanıtları reusable profile answer olmasın.
- [x] Fince, İsveççe ve İngilizce label alias'larını test et; farklı anlam/ülke ve ehliyet-var mı / kategori soruları birbirine eşleşmesin.
- [x] Unknown, declined, conflict ve expired cevapların her biri için ayrı resolver sonucu göster.
- [x] CV/anket çatışmasında hiçbir kaynak otomatik üstün sayılmaz; yalnız çelişen alanı beklet, uygulama kaydında alanı bildir ve kullanıcıyı iki kaynak incelemesine yönlendir.

### Task 3: CV analizi ve tüm sektörlere meslek/beceri önerisi

**Files:**
- Modify: `sampoagent/candidate/service.py`
- Modify: `sampoagent/cv/service.py`
- Create: `sampoagent/cv/ocr.py`
- Modify: `sampoagent/careers/recommendations.py`
- Create: `sampoagent/careers/taxonomy.py`
- Create: `sampoagent/careers/taxonomy_import.py`
- Create: `sampoagent/country_packs/models.py`
- Create: `sampoagent/country_packs/finland/qualifications.py`
- Test: `tests/test_cv_ocr.py`
- Test: `tests/test_cv_records.py`
- Test: `tests/test_career_taxonomy.py`
- Test: `tests/test_country_qualifications.py`

**Interfaces:** `analyze_cv(path, locale) -> CVAnalysis` source spans and confidence ile taslak çıkarır; `recommend_occupations(skills: list[str], *, ignored: list[str], taxonomy: Sequence[TaxonomyOccupation] | None) -> list[OccupationRecommendation]` supporting facts, ESCO essential-skill gaps ve never-auto-target durumunu taşır. `evaluate_job(..., confirmed_records=...)` yalnız confirmed structured records'i explainable rank'e katar; `choose_application_cv(..., records=...)` aynı teyitli geçmişi arşiv-template fit'inde kullanır. `import_esco_package(repository, package_dir, *, version, languages) -> TaxonomyImportSummary` imported CSV index'ini atomik kaydeder.

- [x] Yaygın Fince ve İngilizce bölüm başlıklarındaki beceri, sertifika, dil, ehliyet/ruhsat, iş ve eğitim satırlarını onaysız CV iddiaları olarak çıkar; PDF sayfa/satır/metin aralığını SQLite ve Profile UI'a taşı; de-dupe ve HTML escaping testleri ekle.
- [x] İş ve eğitim satırlarını kişi onayı sonrası yapılandırılmış, tarihli tekrarlı kayıtlara dönüştür; CV/anket/profil uyuşmazlığını alan bazında conflict review'a al. PDF kolon/satır birleştirme ve Swedish section aliases için synthetic fixtures ekle.
- [x] PDF metni yok/scan olduğunda yapılandırılabilir OCR sağlayıcısı sun; sağlayıcı yoksa açık kullanıcı adımı döndür. OCR metni de daima kaynağa bağlı, onaysız taslak olmalı.
- [x] CV dosyası değişince onaylı gerçekler silinmesin; yeni extraction farklı olanları conflict incelemesine al.
- [x] Resmi ESCO indirme sayfasından kullanıcı eliyle alınmış CSV'leri offline içe aktar; version, language, source hash, source URL, reuse statement, required attribution ve adapted-index notice kaydet. Bozuk paket mevcut SQLite indeksini değiştirmediğini sentetik testle kanıtla. Kullanıcı e-postası/portal indirme formunu otomatikleştirme.
- [x] ESCO occupation-skill relationships ile deterministic supporting skill ve essential-skill gap açıklaması üret; bunları yasal ruhsat/eğitim zorunluluğu gibi gösterme. Career UI gerekli attribution ve kaynak sürümünü gösterir.
- [x] Beceri önerisi ya da geçmiş deneyim tek başına search term olmasın; target occupation, explicit career profile ya da açıkça eklenen search preference olmadan iş araması başlamasın.
- [x] Meslek önerilerini ülkeye özgü düzenlemeler, eğitim ve certificates ile genişlet. İlk Finlandiya pack'i seçili sağlık/sosyal bakım, erken çocukluk eğitimi ve özel güvenlik unvanları için resmi kaynaklı yalnızca “authority check” uyarısı üretir; bu uyarılar yasal hard gap/uygunsuzluk kararı değildir. Tam Finlandiya kapsaması ve diğer ülke pack'leri açık iştir.
- [x] Hobi veya CV çıkarımını profesyonel deneyim gibi sunmama testini ekle; meslek kullanıcı seçmeden etkin arama hedefi olmaz.
- [x] Teyitli yapılandırılmış iş geçmişi/eğitimi job ranking ve CV archive fit'ine dahil et; taslak geçmişin puanı veya PDF içeriğini etkilemediğini test et. Yalnız teyitli lisans/sertifika kaydı kendi birebir hard requirement'ını karşılayabilir.
- [x] CV arşivinde işe/kanıta uyum ile parse edilmiş PDF'de teyitli metin bulunma kontrolünü ayrı etiketle; metin kontrolü ATS uyumluluğu veya işe alım başarısı iddiası olmasın. Kod/API adlarını da PDF metin kontrolünü tarif edecek biçimde düzenle; kontrol edilmemiş eski/yüklenmiş CV ile gerçek `0%` sonucunu ayıran SQLite migration ve UI testi ekle.

### Task 4: İlan adaptörleri ve ATS form katmanını genişlet

**Files:**
- Modify: `sampoagent/jobs/runner.py`
- Modify: `sampoagent/jobs/adapters.py`
- Modify: `sampoagent/jobs/service.py`
- Modify: `sampoagent/agents/browser.py`
- Modify: `sampoagent/agents/playwright_adapter.py`
- Create: `sampoagent/agents/forms.py`
- Test: `tests/test_playwright_adapter.py`
- Test: `tests/test_job_verification_ui.py`

**Interfaces:** `FormSchema` semantic version, `FormField` identity/source/options, navigation checkpoint ve page origin taşır; ATS adapter'ı unsupported field için fail-closed döner.

- [x] Semantic `FormSchema` version, page origin/action, field identity/source/options, checkpoint ve schema hash'i ekle. Playwright; label, aria-labelledby, fieldset legend, name, autocomplete ve ilişkili açıklamayı yakalar; etiketsiz required alan tahminiyle doldurulmaz.
- [x] Multistep/checkpoint ve çoklu form context'i tespit et; çoklu form context'i desteklenmez ve aday verisi girmeden manuel incelemeye bırakılır.
- [x] Yalnız Full Autopilot'ta aynı-origin, tek form/tek Next kontrolü, en fazla 8 benzersiz sayfa; sayfa-başına çözümleme/readback/policy kontrolü; ara sayfada upload yok; final sayfada tanımlı CV upload; Smart Approval/Dry Run ilk aday verisi öncesi bekler. Next ile ara kayıt olabileceğini Autopilot izninde açıkla ve final Submit'i tek kez yap; form imzası yanında prepared values da her geçiş/final click öncesi tekrar doğrulanır.
- [x] Radio grupları tek prompt ve seçenek kümesi olarak çıkarılır; teyitli cevap tek seçeneğe tam eşleşirse doldurulur. Checkbox grupları, consent, assessment ve declaration otomatik doldurulmaz; policy'ye bırakılır.
- [x] CV upload alanında exact CV role, formda açıklanmış MIME türü ve size limiti kontrol edilir; CV dışı ek tahmin edilmez. Bu limitler form schema imzasına dahildir.
- [x] Employer account sayfasında başlangıçta dolu ama aday bankasında kaynağı olmayan optional alanları ve seçili ekleri kullanıcı görmeden ezme/gönderme; final adapter click'inden hemen önce pause/izin/kota/ilan politikasını tekrarla.
- [x] Laura/ReachMee/Likeit adlarını yalnız senaryo etiketi olarak kullanan, vendor HTML'i olmayan sentetik form fixture'ları ekle; gerçek Chromium bunları okur ve Laura-şekilli form tam CV hash'iyle izole fake-employer HTTPS E2E hattını kullanır. Gerçek ATS desteği iddiası hâlâ kullanıcı/işveren kontrollü staging E2E gerektirir.
- [x] Public-page source capability/sonuç görünür; Scrapling fetch robots.txt'i izler, terms URL'si kullanıcı inceleyip kaydetmeden fetch etmez, başarıdan sonra en az 60 saniye bekler, hatada kalıcı exponential backoff uygular. Browser-only ve auth gerektiren kaynak otomatik fetch edilmez.
- [x] Form gezintisi URL/origin katmanı HTTPS, credentials, cross-origin istekleri ve same-origin dışı redirect'leri reddeder; authenticated loopback CONNECT proxy bütün DNS A/AAAA cevaplarını fail-closed doğrular ve validated numeric IP'ye bağlanır. Private/mixed DNS, DNS/connect hatası, beklenmeyen origin/port, eksik proxy auth ve non-CONNECT sentetik testleri vardır. Service worker'lar kapalı, WebSocket'ler blokludur. İlan snapshot değişimi mevcut doğrulamayı stale yapar.
- [x] DNS-rebinding yarışını proxy katmanında kapatan IP-pinned CONNECT proxy eklendi; mevcut Windows Chromium build'i için upstream CONNECT arızası ve proxy kapanışı sonrası direct-fallback sentetik testi de var. [ ] Diğer desteklenen browser/platform kombinasyonları, OS-level egress policy/firewall ve işveren kontrollü ATS staging doğrulaması açık. Gerçek removed/expired employer listing kontrolü desteklenen adapter E2E ile doğrulanmalı.

### Task 5: Kalıcı worker, tekilleştirme ve gönderim uzlaştırması

**Files:**
- Modify: `sampoagent/applications/worker.py`
- Create: `sampoagent/applications/worker_controller.py`
- Modify: `sampoagent/applications/runner.py`
- Modify: `sampoagent/db/repository.py`
- Modify: `sampoagent/cli.py`
- Modify: `sampoagent/app/main.py`
- Test: `tests/test_application_worker.py`
- Test: `tests/test_application_automation.py`
- Test: `tests/test_worker_controller.py`
- Test: `tests/test_worker_controller_ui.py`

**Interfaces:** worker lease owner + heartbeat; application claim unique(candidate, canonical_job); attempt transitions atomic; unknown attempts enter reconciliation, never ready queue.

- [x] `jobs.fingerprint` + `application_claims.job_id PRIMARY KEY` mevcut canonical/unique sözleşmesi incelendi; `BEGIN IMMEDIATE` kuyruk eklemesi iki SQLite bağlantısından eşzamanlı thread testiyle tek kayıt üretiyor.
- [x] Kaynak hataları için persisted exponential backoff (60 saniyeden başlayıp altı saate kadar) ekle; başarıda sıfırla, browser-only kaynağı yine hiç fetch etme. Tek yerel worker ve seri kaynak/başvuru akışı mevcut concurrency üst sınırını 1'de tutar.
- [x] CLI `--watch` loop ve pause kontrolleri var; adapter final click öncesi authorization gate'i çağırır, pause olursa tıklanmadan önceki rezervasyon iptal edilir. Aktif uzun navigation sırasında anında abort ve iptal token'ını tüm provider çağrıları boyunca geçirmek açık sınırlamadır.
- [x] Worker crash/lease expiry sırasında PREPARING ve SUBMITTING durumlarını ayır; sahiplik token'ı ile eski worker'ı fence et; yalnız submit'ten önce güvenle resume et.
- [x] UNKNOWN/UNVERIFIED gönderimi e-posta ya da işveren onayıyla elle reconcile edilene kadar asla retry etme; yalnız kesin tıklama-öncesi iptal edilmiş rezervasyon yeniden denenebilir.
- [x] Aynı application attempt/daily slot için bağlantılar-arası yarış testleri ve adapter içindeki son tıklama öncesi pause/authorization callback'ini ekle.
- [x] UI'da source son durumu/retry window, queue age/wait reason, worker last heartbeat/next run ve emergency stop/resume görünür.
- [x] `sampoagent run` içinde `AutopilotWorkerController` ekle; yalnız aktif mode + Dry Run kapalı + pozitif limit + geçerli Autopilot grant (ya da paket-onaylı Smart Approval) koşulunda başlat, grant/pause değişince durdur ve shutdown'da kapat. Her cycle ayrı SQLite bağlantısı kullanmalı; TestClient varsayılanında browser/worker başlamamalı.
- [x] Form navigation öncesinde aday/answer/scope fingerprint'i al; denetimden sonra, her alan/upload öncesinde ve final click'te karşılaştır. Grant iptal edilip yeni profille tekrar açılırsa eski çözümlenmiş form paketi gönderilmemeli.
- [x] Autopilot grant'i açık target/search scope olmadan reddet; her job'u seçili target occupation/career profile/explicit terms ve preferences'a karşı form açılmadan önce kontrol et; career profile/source URL-policy değişikliği ve 30 günden uzun grant'i reddeden testler ekle.

### Task 6: Gmail/Outlook ile e-posta başvurusu ve takip

**Files:**
- Modify: `sampoagent/integrations/email_oauth.py`
- Modify: `sampoagent/integrations/mailbox.py`
- Create: `sampoagent/integrations/email_send.py`
- Create: `sampoagent/integrations/outbox.py`
- Modify: `sampoagent/db/repository.py`
- Modify: `sampoagent/app/main.py`
- Test: `tests/test_email_send.py`
- Test: `tests/test_email_outbox.py`

**Interfaces:** read mailbox permissions remain separate. `create_email_draft()` prepares; `send_approved_email(outbox_id)` requires provider account, recipient, subject/body/attachment hashes and current send consent.

- [x] Gmail için ayrı `gmail.send` izin akışı ve Graph için delegated `Mail.Send` izni eklendi; eski read token otomatik yükseltilmez, kullanıcı ayrı izin akışıyla bağlanır.
- [x] Multi-account kayıtlarını provider, subject, scopes, token expiry ve protected secret reference ile sakla; token plaintext/logs never. Provider+subject hesapları, görünür seçili varsayılan sender, şifreli token ciphertext ve hesap-sınırlı izin parmak izi uygulandı. Gerçek hesaplarla OAuth/send ve delivery/Sent-folder mutabakatı ayrı release gate olarak açık.
- [x] Alıcı yalnız güncel verified ilanı adayı tarafından yeniden açıp onayladıktan sonra kabul edilir; uygulama serbest metin alıcıyı ilana otomatik çıkarmıyor, domain belirsizliğinde otomatik gönderim yapmıyor.
- [x] Mesaj paketi dil, role, employer, provider/connected-at binding, alıcı, konu/gövde, job snapshot, aday-kapsam hash'i, CV dosya yolu/adı ve SHA-256'yı hash'ler ve şifreleyerek bağlar; ekler yalnız güvenli PDF CV'dir.
- [x] SQLite outbox tek application/idempotency kaydı tutar; READY→SENDING→ACCEPTED/UNKNOWN/FAILED_FINAL geçişi transaction içindedir. Timeout, 5xx, süreç kesintisi ve belirsiz cevap UNKNOWN olur; no retry. Outlook 202 ve Gmail başarılı cevabı `ACCEPTED`, teslim değil.
- [x] Inbox okuma uygulama durumunu kendiliğinden değiştirmez; olası cevap aday tarafından başvuruya bağlanıp elle onaylanır. Otomatik sender/job/title eşleştirmesi veya otomatik kabul yoktur.
- [x] Takip mesajı/otomatik recruiter yanıtı/teklif kabulü uygulanmadı; ayrı özellik ve açık izin olmadan hiçbir takip postası çıkmaz.

### Task 7: Yetki paneli, güvenlik ve pilot doğrulaması

**Files:**
- Modify: `sampoagent/app/main.py`
- Modify: `sampoagent/db/repository.py`
- Modify: `sampoagent/cli.py`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Create: `docs/SECURITY.md`
- Test: `tests/test_automation_controls.py`
- Test: `tests/test_security_boundaries.py`
- Test: `tests/test_backup_restore.py`

**Interfaces:** UI shows effective policy state from repository, never from stale browser form state. Autopilot grant is time-limited, revocable, and bound to profile/target/source/preferences/quota fingerprint.

- [x] Onboarding end state shows CV analysis, recommended roles, candidate skills, search scope, permission choices, selected mode, quota and stop rules before enabling discovery.
- [x] Settings explains the explicit target/search-scope requirement and links to career suggestions before Autopilot authorization.
- [x] Current handoff: the 63-question landing review embeds local CV upload, then links through source-evidence review and career suggestions; only explicit target selection activates discovery.
- [x] Unify those pages into a guided result/review screen with evidence-backed role suggestions, configurable search scope, quota/stop summary, and mode/authorization selection before enabling the worker.
- [x] Export, backup/restore, retention and delete flows include CV copies, answer bank, submission evidence, encrypted OAuth-token ciphertext and generated documents with explicit policy. `.sampobak` is passphrase encrypted; key and browser session are excluded; downloaded-file retention is user-controlled; restore/erase require stopped app and exact CLI confirmation.
- [x] Verify secret and PII redaction in browser-proxy failure output, worker exceptions, activity/audit storage and reads, and user-facing errors. `activity_log` persists only allowlisted action codes plus a fixed generic detail; repository/UI reads mask legacy values; Settings provides an exact-confirmation cleanup action preserving event codes/timestamps. Previously downloaded backups remain user-managed; no live database was opened or rewritten.
- [x] Define the SampoAgent Codex skill's learning rule: at the start of each relevant task, consult only the opted-in aggregate when the exact active database is already known; use it only to explain patterns or break ties among qualified roles/CVs, never mutate candidate facts, and never create detached tasks.
- [x] Expose and integrate a user-approved, read-only local learning digest with default-off Settings control, exact-path-only access, five-outcome cohort suppression, and PII exclusion. If active DB identity or consent is missing, skip without searching/asking; the command independently checks consent.
- [x] Synthetic scenario matrix: 20+ real-Chromium form shapes across FI/EN/SV plus runner/security fixtures for required/optional fields, radios, multiple uploads, changed redirects, network disconnect, CAPTCHA, expired job, profile conflict, scope revoke and duplicate workers. Synthetic coverage does not qualify a real ATS adapter.
- [ ] Supported adapter staging E2E proves correct form, correct file checksum, one submit click, same-origin confirmation and truthful final status.
- [x] Define the V1 availability contract and safely resume after sleep/network loss; any OS login-start integration is explicit opt-in, least-privilege and removable, not silently installed. Full Autopilot cannot claim to run while the machine or interactive user session is unavailable.
- [x] Run the complete pytest suite, Python compile check, CLI help smoke, and `git diff --check`.
- [x] Run a manual local-UI smoke, review SQLite migration/rollback behavior, and scan distributable files for known candidate identifiers and common secret patterns. Synthetic backup/restore tests and an isolated temporary-database UI smoke passed; unknown personal data cannot be ruled out by pattern scanning alone.
- [x] Keep Full Autopilot disabled by default.
- [ ] Enable any Full Autopilot pilot only on supported, verified source/adapter pairs after all zero-unauthorized-submit gates pass.

## Execution notes

These notes are chronological; later dated progress updates supersede earlier status statements.

- Task 1's initial implementation and current revalidation increment are locally tested: Smart Approval persists across restarts, stale form/CV hashes return to review, and the browser adapter rechecks live upload bytes before the final click. This is not live-service verification.
- CAPTCHA queue UX now reports the waiting count on the dashboard and routes the candidate to individual manual tasks. Full Autopilot continues with other eligible queue items; CAPTCHAs are never solved by the worker.
- CV auto-selection rechecks archived bytes, requires at least 85% role/language/evidence fit plus every detected hard requirement, and may use a bounded checksum-linked outcome signal only among CVs within five fit points of the best eligible candidate. New job CVs prioritize confirmed vacancy-relevant skills and use a paginated one-column PDF layout; they are text-reparsed and archived.
- Task 2 now has stable typed question metadata, explicit answer states, safe country scoping, optional expiration, a legacy SQLite backfill that holds ambiguous work-authorization answers, and resolver/UI tests. The remaining Task 2 gates are localization, broader ATS question fixtures, employer-specific answer management, and explicit CV-vs-answer conflict review.
- Confirmed answer lists now create source-linked facts for role matching; hobby/transferable skills remain separate from CV claims but may generate role/search recommendations. Reconfirming a value deactivates facts created by the superseded answer. This feeds the current small fallback role catalog; the ESCO taxonomy/import remains Task 3.
- Historical note, superseded 2026-09-30: local CV outcome signals previously required five results. The current local engine uses each candidate-confirmed outcome with prior shrinkage; the five-outcome minimum now applies only to the opt-in Codex aggregate digest. Other items in this older phase list are superseded by the dated progress notes below.
- Task 1 now passes its listed 36-test targeted suite, including adapter-level CV upload drift, Smart Approval CV re-review, and restart/stale-form cases. Changes remain uncommitted in the preserved working tree; no local submission or live ATS test was run.
- Task 2 is complete. Tasks 3–7 remain follow-up implementation phases. The pilot exit criteria in the spec are hard gates, not a schedule promise.
- Task 2 answer-bank increment: employer-scoped reuse is allowed only for mapped salary/notice/start-date/shift answers and requires exact employer scope. Motivation/assessment/adjustment/demographic/privacy prompts remain unmapped, high-risk as appropriate, and non-reusable. Synthetic FI/SV/EN fixtures cover the common question families and work-country distinctions. A confirmed-CV licence conflict pauses only the implicated application field and the Applications page links to CV facts and questionnaire-answer provenance for candidate review. Focused verification: 75 passed, 1 upstream Starlette/httpx deprecation warning.
- Local verification on 2026-09-29 before the taxonomy increment: `py -m pytest -q` → 236 passed; `py -m compileall -q sampoagent`, `py -m sampoagent --help`, and `git diff --check` succeeded. Focused and final verification for the taxonomy increment are appended below. One upstream Starlette/httpx deprecation warning remains.
- Changes in this working tree include pre-existing user work and are intentionally not committed or pushed by this plan.

### Progress update — ESCO catalog and explicit search activation (2026-09-29)

- The older execution note above predates this increment: an optional, user-downloaded ESCO CSV importer and local occupation-skill index are now implemented and covered with synthetic package tests. Current catalog, attribution and license/reuse handling is described in the design spec and README.
- The offline starter occupation matcher includes Finnish/English aliases for 24 representative roles; it remains explicitly non-exhaustive. Full occupation breadth continues to use the optional user-downloaded local ESCO index; candidate skill data is not sent to ESCO's web API.
- Career recommendations consume confirmed professional and transferable skills, but recommendations and historical job titles no longer become searches by themselves. Only user-enabled target occupations, explicit career profiles, or explicit search preferences create search terms.
- Still open in Task 3: structured CV analysis, OCR, field conflict review and country guidance; these are now implemented as described in the dated progress notes below. Live verification against a complete Commission package remains open.

### Progress update — CV evidence and onboarding handoff (2026-09-29)

- The previous note predates this increment: common Finnish/English section headers now produce deduplicated, unconfirmed candidate claims for skills, languages, licences, certificates, work-history lines and education lines. Source page/line, character span and excerpt persist in a backward-compatible `facts.evidence_json` column and are rendered safely in Profile.
- The landing/onboarding review step now embeds local CV upload and explains the sequence: review source-backed claims → consider career suggestions → explicitly activate chosen roles. Discovery still stops with no search terms; profile history and suggested roles alone cannot trigger a source request.
- The above CV evidence increment has since been extended: OCR provider integration, normalized recurring work/education records and date parsing, Swedish aliases, PDF reading-order heuristics, and explicit candidate conflict choices now have implementation and synthetic tests.
- Verification on 2026-09-29 before the final CV increment: `py -m pytest -q` → 257 passed. The next whole-suite run found three stale test doubles/assertions that were updated to the current browser-submit and safety-copy contracts; final verification is appended below. No live ATS submission or external account test was run.

### Progress update — CV records, OCR and PDF layout (2026-09-29)

- CV extraction now creates repeated, unconfirmed work/education drafts with normalized date ranges, current-role flags, organization, location, details, and source evidence. Profile supports per-record confirm/reject and explicit conflict choices. Existing confirmed history is preserved until the user decides how to resolve a conflict. Generated CVs carry confirmed dates and organizations.
- Swedish section aliases and synthetic Swedish, two-column, and hyphen-wrapped PDF fixtures are present. PyPDF layout mode is used only when repeated stable whitespace suggests two columns; ambiguous page geometry falls back to plain extraction. Only explicit hyphenated word wraps are rejoined.
- Optional OCR supports disabled/environment/local Tesseract providers. OCR is local, time/page bounded, page-linked and unconfirmed. Missing binaries produce a visible manual-entry instruction without creating candidate facts.
- Finland qualification advisories use current official sources for selected health/social-care practice-right checks, listed ECEC qualifications, and private-security approvals. Each advisory is dated and linked to the authority; all use `is_legal_hard_gap=False`. They do not assess candidate eligibility. Other jurisdictions and complete Finland regulated-profession coverage remain open.
- A whole-suite run before refreshing old adapter/test doubles found three expected-contract mismatches: worker fakes did not accept the new `expected_uploads` keyword and a test compared the unchanged safety copy case-sensitively. Those fakes/assertion were updated; final full verification is pending.

### Progress update — semantic ATS form capture (2026-09-29)

- Added `FormSchema` v1.1 with semantic fields, option sets, accessible-name source, descriptions, same-origin action, page origin, step checkpoint, file accept types, explicit size limit and multiple-file semantics in the signed schema. Playwright now recognizes associated labels, `aria-labelledby`, fieldset legends, `aria-describedby`, name and autocomplete; no coordinate-based guesses are used.
- Radio groups are one prompt and option set, and a confirmed value fills only if exactly one option label matches. Checkbox groups remain unsupported/manual. Legal, privacy, assessment and declaration labels still pass through the high-risk policy.
- Visible Next/multistep state, multiple form contexts and cross-origin form actions pause before candidate data entry. Page-by-page multi-step processing and snapshot replay remain open; the adapter does not claim it supports these pages.
- CV file fields inspect accept tokens and explicit size hints. Wrong types/oversized files stop before upload; the exact uploaded name/hash remains bound to package review and is re-read immediately before the click.
- A real local headless Chromium test uses only intercepted synthetic HTML (no live employer request) to cover accessible names, radio grouping, file constraints and step detection. Focused tests pass; final suite/CLI diff verification follows after related work.
- One reviewed CV is never treated as satisfying a multiple-file input; an unmatched required non-CV attachment stops before candidate fields are entered.

### Progress update — worker fencing, source cooldown and operator status (2026-09-29)

- Queue insertion was already transactional and unique per canonical job; new two-connection thread tests prove that two simultaneous inserts create one application only. Per-application preparation now has an atomic `PREPARING` state and owner token. Expired worker lease recovery may safely return pre-click preparation to READY, while an old process is fenced from submit reservation.
- `SUBMITTING` remains irreducible/unknown after a process crash and is not retried. A browser refusal before click cancels its reservation only when the adapter explicitly guarantees no click; cancelled reservations do not consume the daily quota. A callback reruns pause, current grant, scope, listing freshness and quota after form/attachment re-read, immediately before Playwright click.
- Existing optional values already present on the employer page are not overwritten if no confirmed candidate value maps to them. Unexpected preselected file attachments stop before data entry.
- Discovery now persists per-source exponential cooldown from 60 seconds to six hours and clears it on successful checks. The current worker/source pipeline is sequential with at most eight automatic sources per discovery cycle.
- Dashboard/queue now show worker status, last heartbeat/result, watch next-run time, queue age and wait reason; Sources shows last attempted check and retry window. This is observability for the local single-worker configuration, not multi-worker scaling.
- Remaining Task 5 gap is configurable source/employer-level parallelism and cancellation that interrupts an already hung browser navigation/provider call. The current serialized worker deliberately caps active submissions at one.

### Final verification — 2026-09-29

- The full local suite now passes: `py -m pytest -q` → 311 passed, with one upstream Starlette/httpx TestClient deprecation warning. The only failure in the first whole-suite run was a stale queue-UI assertion expecting the old single-status-cell markup; a focused rerun confirmed the updated `READY` + `QUEUED` presentation, then the full suite passed.
- `py -m compileall -q sampoagent`, `py -m sampoagent --help`, and `git diff --check` succeeded. No live employer page, ATS staging account, real submission, or mail-send OAuth was tested.
- Remaining release gates are Tasks 4/6/7: supported-ATS staging E2E and expanded form fixtures; delegated Gmail/Graph send scope with idempotent outbox; unified onboarding authorization summary; data export/backup/restore/delete and log-redaction checks; security matrix and supervised pilot. The single local worker and instant cancellation of a hung third-party navigation remain explicit limits.

### Progress update — encrypted local data lifecycle and design gap audit (2026-09-29)

- Settings backup and `sampoagent backup` create an authenticated `.sampobak` export: SQLite online snapshot plus allowlisted CV/application folders, SHA-256 manifest, scrypt-derived AES-256-GCM envelope, at least 12-character passphrase never stored. Restore prompts for the passphrase and exact confirmation, validates the ZIP/path/hash/SQLite contents before replacing selected state, stages rollback data, preserves the current browser profile, and holds cross-process locks. Erase has a separate exact confirmation and only removes the specified database/sidecars and SampoAgent-managed folders.
- Download retention is deliberately user-owned; `.env`/the encryption key, browser session, unrelated storage files, and downloaded backups are not silently moved or erased. The settings and security docs disclose what must be reconnected after restore and what erasure leaves behind.
- Synthetic lifecycle tests cover export inclusion/exclusion, encrypted passphrase authentication, CLI backup/restore/erase round-trip, lock exclusion, invalid/tampered/path-traversal archives, deletion scope, and the settings route. Candidate-specific archive filenames are ignored by Git without removing the existing local files.
- Final verification for this increment: `py -m pytest -q` → 358 passed, 1 upstream Starlette/httpx deprecation warning; `compileall`, CLI help, PII-pattern scan for the previously supplied candidate identifiers, and `git diff --check` passed. This did not use candidate records or test an employer submission.
- Remaining production gates: unified onboarding result/scope/permission screen; exhaustively tested PII/secret redaction; 20+ synthetic ATS scenarios; employer-controlled staging E2E; OS-level DNS-pinned egress; separate Gmail/Graph send grants plus idempotent outbox; optional user-session auto-start/availability contract; and supervised end-to-end pilot. The app is not yet universally automatic and Full Autopilot must remain off until supported source/form pairs pass those gates.
- The pre-existing workspace changes and untracked application archive files were preserved; nothing was staged, committed, pushed, or removed.
- Codex retrospective now has a default-off local consent setting and an existing-database-only `learning-summary` CLI. Read-only output aggregates role family and CV language after a five-outcome minimum and omits all candidate/employer/job identifiers and CV content; the skill uses only this command and never searches raw database records. Application receipts no longer distort the learned hiring-outcome signal. Focused tests: 9 passed; complete-suite verification follows.
- The skill's synthetic pressure scenario was run both before and after its command/consent instructions: in both cases the agent refused to inspect a synthetic local history while consent was off, and after the edit it named the exact gated read-only command for the enabled case. This validated the specific new integration without exposing real profile data.
- Final verification for the learning-handoff increment: `py -m pytest -q` → 349 passed, 1 upstream Starlette/httpx deprecation warning; compileall, CLI help, learning-summary CLI help, and `git diff --check` exited 0. No local candidate database or employer site was opened.
- Confirmed structured work/education and credential records now feed deterministic job ranking and archived-template fit; only exact confirmed certificate/licence records can satisfy parsed credential requirements. Generated PDFs and scores exclude unconfirmed history. Regression tests were observed RED against the previous code and then GREEN.
- Latest whole-suite verification after that increment: `py -m pytest -q` → 317 passed, 1 upstream deprecation warning; compileall, CLI help and diff check exited 0. No live employer site or application was touched.
- App-managed worker follow-up: `sampoagent run` now attaches one lifecycle worker; Settings and emergency-stop changes reconcile it, while normal `create_app()` remains inert unless a worker is injected or management is explicitly enabled. Candidate/scope fingerprint is checked after navigation, before each form value/upload, and at final click so refreshed grants cannot reuse stale values. Focused controller/UI/CLI/worker safety suite → 29 passed; application automation with controller checks → 31 passed. Latest whole-suite run → 326 passed, 1 upstream Starlette/httpx deprecation warning; compileall, CLI `--help`, and `git diff --check` exited 0. No browser was launched and no employer page, account, or application was touched.

### Progress update — exception and access-log redaction (2026-09-29)

- User-facing profile/source/CV/answer/CAPTCHA/settings/email failure notices no longer interpolate exception text. The onboarding confirmation error is fixed text; failed discovery records, worker state, and automation CLI failures likewise omit provider exception messages and CLI chaining.
- Unhandled web request failures return a generic 500 with a random reference and log only the reference plus exception type. Local Uvicorn access logs are disabled because email OAuth callback codes and state appear in query parameters.
- Synthetic tests observed RED→GREEN for profile exception echo, discovery/provider exception persistence, CLI worker exception/chaining, web 500/logging, and access-log configuration. Focused verification covered 33 tests across security, lifecycle, CLI, discovery, and email UI; whole suite: `py -m pytest -q` → 364 passed with one upstream TestClient deprecation warning.
- This is not yet a completed redaction audit: underlying browser/driver diagnostics and user-provided application evidence values still need supervised review; the Task 7 checkbox remains open. No real profile DB, browser session, employer site, or submission was used.

### Progress update — guided search and permission review (2026-09-29)

- Added `/onboarding/ready` as one review step showing CV count and confirmed/unreviewed fact totals, confirmed skills, supporting evidence for recommendations, saved/active target roles, source/query coverage, search preferences, current worker/grant state, daily quota, and automatic stop rules.
- Saving roles and search preferences is a separate action that keeps Dry Run on, revokes any old Autopilot grant, starts no discovery request, and never starts a worker. Live Smart Approval requires a separate explicit live-mode checkbox. Full Autopilot additionally requires its own unchecked-by-default, 30-day scoped authorization, a profile, explicit scope, and positive quota.
- Role selections are revalidated against current recommendations and saved targets; malformed or invented role values return 422 before any scope changes. Target replacement is stored atomically. Reopening the page shows an existing grant without pre-checking renewal consent.
- Dashboard, profile, navigation and the questionnaire review now lead into the guided screen. A user still explicitly starts a search from Jobs; GET remains read-only.
- TDD RED→GREEN: 4 route tests cover the effective summary/read-only GET, dry-run scope save, unknown-role atomic rejection, and independent Full Autopilot permissions; the existing landing/onboarding and automation-control tests were included. Focused suite: 27 passed with the existing upstream Starlette/httpx deprecation warning.
- Manual browser smoke against an isolated temporary database verified the rendered empty-profile state, default Review Everything/Dry Run, disabled authorization, source scope, role selection area, stop rules, and distinct Save/Enable controls. No profile data, employer site, source fetch, browser session, or external submission was used.
- TDD RED→GREEN: single-file CV upload now refuses multiple-file inputs and unmatched required attachments before candidate data entry. Synthetic real-Chromium fixtures cover more than 20 FI/EN/SV form shapes; runner fixtures include network loss, CAPTCHA, expired jobs, conflicting facts, changed redirects/scopes and concurrent duplicate work.
- A repeat manual local smoke again used an isolated temporary database. Full suite before the final network-loss regression: 371 passed; then the network-loss, multi-upload and Chromium matrix cases passed as a focused set (3 passed). Compileall, CLI help, diff check, known-candidate-identifier scan, and common-secret scan succeeded; ignored local databases, archives, CVs and browser state remain excluded.
- Remaining Task 7 release gates: supervised review of historical user-entered evidence and native browser diagnostics; employer-controlled ATS staging E2E; IP-pinned browser egress; delegated Gmail/Graph send scope and idempotent outbox; user-session availability/auto-start contract; and a supervised pilot. Synthetic form tests do not qualify employer adapters, the screen does not start discovery on GET, and no external submission was made.

### Final verification — 2026-09-29

- Latest full local suite: `py -m pytest -q` → 372 passed, one upstream Starlette/httpx TestClient deprecation warning. `py -m compileall -q sampoagent`, `py -m sampoagent --help`, `py -m sampoagent run --help`, `py -m sampoagent learning-summary --help`, and `git diff --check` exited successfully.
- Known supplied candidate-identifier and common-secret pattern scans had no matches in distributable files. `.env`, SQLite databases, personal CV/application storage, QA state, browser data and downloaded archives are ignored and were not included.
- Final browser smoke used a disposable empty SQLite DB; form-matrix and network-failure tests used synthetic HTML/data. No live employer page, candidate database, mailbox, ATS staging account or application submission was accessed.
- Curated source, tests and documentation were committed as `4f3a2b4` and pushed to `origin/codex/full-application-automation`; local databases, CV/application files, browser state and ignored archives were not uploaded. Employer-controlled staging, IP-pinned browser egress, delegated mail-send/outbox, availability/auto-start policy, supervised review of all historical evidence/native browser diagnostics, and the supervised pilot remain release gates; synthetic tests do not qualify a universal Full Autopilot claim.

### Historical progress — initial separate email-send OAuth and outbox (2026-09-30; superseded)

- Added separate Gmail `gmail.send` and delegated Graph `Mail.Send` OAuth flows; existing read tokens/scopes remain unchanged. Cancellation does not replace the read or send connection. Send-token refresh updates only encrypted token ciphertext, preserving the permission binding.
- Email drafts require a fresh verified listing, archived application-specific PDF, and explicit candidate confirmation that its one recipient was copied from the current employer page. The encrypted package binds recipient, subject, body, language, role/employer, listing fingerprint, candidate/search scope, send provider/account connection timestamp and PDF name/hash. Drafts use a unique SQLite outbox row and `EMAIL_READY` queue state, preventing browser and email submission paths from targeting one application concurrently.
- Added separate optional 30-day email-Autopilot consent bound to the active general grant, daily limit, scope and send connection. It remains off unless explicitly checked. The worker sends only candidate-confirmed email drafts when that additional grant is current. Exact-package manual confirmation remains available without the email-Autopilot grant.
- Outbox reservation atomically claims a daily slot and one provider attempt. Gmail success and Graph 202 are `ACCEPTED` only, never delivery claims. Timeouts, 408/5xx, unexpected responses and stale SENDING after process interruption become `UNKNOWN`/`DO_NOT_RETRY`; explicit provider rejection is `FAILED_FINAL`. No outbox request is retried. A shared direct attachment cap of 2 MiB keeps Graph's single-call JSON file attachment below its documented 3 MB ceiling.
- Provider email calls, OAuth refresh, and Graph/Gmail callbacks were mocked; no real OAuth account or message was used. Sender account email identity is not visible under the least-privilege send scopes. The app does not discover the employer's email address from its page: candidate address confirmation remains required for each email draft. Multi-account slots, account identity display and Sent-folder reconciliation remain incomplete; real mail delivery is unverified.
- TDD added tests for send scopes/cancellation, encryption and idempotency, candidate confirmation, cancellation/replacement, exact-package confirmation, CV/scope staleness, daily reservation, crash recovery, independent Autopilot permission, no browser/email queue overlap, Gmail MIME and Graph JSON requests, one-shot acceptance/unknown outcomes, size/recipient validation, and settings consent. Focused safety suite: 31 passed. Whole suite: `py -m pytest -q` → 397 passed, one upstream Starlette/httpx warning. `compileall`, CLI help, and `git diff --check` passed.
- Remaining release gates: provider sender identity/account selection and multi-account data model; actual OAuth/send under user-controlled accounts; official employer recipient verification without relying solely on candidate entry; deterministic Sent-folder reconciliation; multi-language/high-stakes mail text review; employer ATS staging E2E; DNS-pinned browser egress; availability/auto-start; supervised redaction review and pilot. Synthetic tests are not live mail or universal-autopilot certification.
- Superseding implementation update (2026-09-30): provider identity and multi-account/default sender selection are now implemented; the former candidate-copy requirement for local Full Autopilot email is replaced by deterministic FI/EN/SV extraction from the exact current verified listing. Only a single contextual application-email recipient qualifies; missing/ambiguous address, additional required material, negated/unsupported instruction or stale/missing evidence is held without browser fallback. Provider identity, recipient provenance/listing hash, default account, scope, and job-specific CV checksum are bound to the encrypted package. The parser recognizes common affirmative/modal/passive forms; likely but unsupported application+email wording is held, while a separate online-application path plus contact email remains a browser job. Full verification: 442 tests passed (one upstream deprecation warning); compileall, CLI help and diff check passed; independent read-only code review had no remaining findings. See the focused [design](../specs/2026-09-30-email-autopilot-identity-and-recipient-design.md) and [implementation plan](2026-09-30-email-autopilot-identity-and-recipient.md). Historical notes above describe the previous state and are superseded where they conflict. No real OAuth, email or application was used; release gates remain open.
- Codex skill follow-up: updated the repository and installed personal skill, README, and engineering handoff so the opted-in digest is a proactive side task in the active Codex task, rather than an optional retrospective. Fresh-context pressure tests passed 5/5 for the known active DB + consent-on path and 1/1 for the unknown-path safety branch (skip; do not search or ask solely for learning context). `py -m pytest -q` → 397 passed (one upstream Starlette/httpx deprecation warning); `py -m compileall -q sampoagent`, learning-summary CLI help, and `git diff --check` passed. No database, candidate record, employer site, or application was accessed. The skill uses only the existing read-only CLI and bounded aggregate output.

### Progress update — pinned browser HTTPS egress (2026-09-30)

- The Playwright browser now uses an authenticated ephemeral loopback CONNECT proxy. Application mode only tunnels to the exact opened origin; the manual browser-login mode is limited to public HTTPS port 443. The proxy rejects any DNS answer set containing a non-global address and dials a validated numeric IP, leaving employer TLS end-to-end. Browser route checks remain a second policy layer; proxy shutdown is tied to browser shutdown and startup failure.
- The proxy and browser integration use only the Python standard library plus the existing optional Playwright dependency; no additional package or live service is required. Synthetic regression tests cover mixed/private DNS, numeric-IP dialing, wrong origin/port, non-CONNECT traffic, missing proxy auth, resolver/connect failures, startup/close cleanup, and request route behavior.
- Verification: focused browser/automation suite → 66 passed; full suite → 453 passed with the existing Starlette/httpx deprecation warning; `compileall`, CLI help, and `git diff --check` pass. No live employer, candidate DB, browser profile, OAuth account, email, or application was used.
- This is not a complete OS egress sandbox. Chromium direct-fallback behavior, platform firewall/DNS containment, employer-controlled ATS staging, supervised historical/native browser log redaction review, and end-to-end pilot remain release gates. Universal Autopilot is not certified.
- Follow-up after independent review: malformed CONNECT authorities now fail before DNS, active tunnels are terminated on proxy/context close, and stale DNS results cannot connect after a proxy restart. The review found two Important issues and no Critical findings; both were fixed test-first. Focused browser/application safety suite → 72 passed; full suite → 459 passed, one upstream deprecation warning.
- Activity privacy follow-up: new activity events store allowlisted action codes and fixed generic details; UI/repository reads mask old details. Settings now exposes a local-token-protected exact `REDACT` action that replaces only legacy detail strings while preserving event codes/timestamps; previously downloaded backups are unchanged. Focused privacy/backup/CSRF suite → 23 passed; latest `py -m pytest -q` → 464 passed, one upstream deprecation warning. No live candidate database was opened or modified.
- V1 local availability is explicit in Settings, README and this design: the app and interactive user session must be present; no OS-startup task is installed. The worker keeps the durable queue, closes/recreates a possibly stale browser after initialization/cycle errors, waits the ordinary poll interval, and re-checks current authorization before resuming. Recovery preserves the existing rule that interrupted submit/send attempts become `UNKNOWN` and never retry. Synthetic controller tests cover transient browser-factory and cycle failures; they do not substitute for platform-specific Windows sleep/wake, network-adapter or Chromium direct-fallback testing. Focused controller/UI/application-worker verification: 21 passed; full suite: `py -m pytest -q` → 466 passed, one existing Starlette/httpx deprecation warning; compileall, CLI help and `git diff --check` passed.
- Synthetic browser-form E2E follow-up (2026-09-30): corrected pre-fill handling so native HTML `required` controls do not block preparation merely because the browser initially marks empty fields invalid; validation still runs after confirmed fields and selected CV are filled. A local fake-employer Chromium test receives the actual multipart HTTPS request at a loopback fixture, blocks non-fixture browser egress, and verifies exact CV bytes/checksum, exactly one POST, and a same-origin receipt. It does not qualify any user-controlled ATS/employer adapter; Task 4 and Task 7 staging/release gates remain open. Focused `tests/test_application_automation.py` + `tests/test_playwright_adapter.py`: 48 passed; full suite: 467 passed with the one existing upstream Starlette/httpx deprecation warning. `compileall`, CLI help and `git diff --check` passed.
- Verified-job CV enqueue regression (2026-09-30): the automated queue enumerated jobs through the lightweight `rows("jobs")` view, which omits the verified-snapshot hash required by the active-listing gate. As a result, eligible verified jobs were silently skipped before job-specific CV generation. The queue now resolves each candidate through the authoritative `repository.job(job_id)` view before checking verification and eligibility. A regression test first demonstrated the empty queue, then passed with the fix. The local HTTPS/Chromium E2E now also starts from a weak archived CV, exercises automatic enqueue and generation from confirmed demo facts, verifies the generated PDF is archived with the same checksum, and confirms the exact bytes reach the synthetic employer once. Focused application/CV/worker/Playwright suite: 69 passed; full suite: 468 passed with one existing upstream Starlette/httpx deprecation warning; `compileall`, CLI help and `git diff --check` passed. Synthetic fixture only; no candidate database, live employer, ATS account or real application was used. Employer-controlled staging, OS egress/fallback validation, supervised privacy review and the pilot remain open gates.
- Chromium proxy enforcement follow-up (2026-09-30): explicitly pass `<-loopback>` in Playwright's proxy bypass configuration so Chromium's implicit localhost/link-local bypass is removed by our policy rather than depending on Playwright-version defaults. The synthetic employer E2E now goes through the real pinned CONNECT proxy; Chromium itself is configured with a test-only `~NOTFOUND` host rule so the fixture hostname cannot escape to public DNS. After a successful exact-file submission, shutting down the proxy makes a subsequent navigation fail and produces no second fixture GET. Focused browser/application/proxy suite: 64 passed; full suite: 468 passed with one existing upstream Starlette/httpx deprecation warning; `compileall`, CLI help and `git diff --check` passed. This proves the isolated current Chromium/proxy path only; supported-platform direct-fallback and OS firewall/egress tests remain open.
- CAPTCHA completion race follow-up (2026-09-30): two concurrent local requests could both read the same task as `IN_PROGRESS` before either wrote, producing conflicting terminal status or duplicate manual-submission evidence. A deterministic two-connection race test failed with both requests accepted; `finish_captcha_task` now takes `BEGIN IMMEDIATE` before reading state and writes the terminal task/application/evidence/timeline changes atomically. The second finisher re-reads `COMPLETED` and is rejected. Focused controls/worker/application suite: 58 passed; full suite: 469 passed with one existing upstream Starlette/httpx deprecation warning. No employer or candidate data was used.
- CAPTCHA one-at-a-time UI follow-up (2026-09-30): route-level regression tests showed that every waiting CAPTCHA card rendered both a start button and an employer link even though the repository serializes active tasks. The queue now offers a start action only for the next waiting task and withholds all employer links until an item becomes active; other cards say they are waiting until the current CAPTCHA task finishes. After recording the active task's outcome, the next queued task and its link become available. This improves the manual handoff without solving/bypassing a challenge or stopping other eligible worker jobs.

### Progress update — guarded multi-step Full Autopilot forms (2026-09-30)

- Local Full Autopilot may traverse at most eight distinct same-origin pages, only when every intermediate page has one accessible form and one unique Next/Continue control. It reloads and revalidates the current listing, candidate scope, answers, form signature and live values before each navigation; intermediate uploads are not supported, and only a clearly identified CV may be attached on the final page. Smart Approval and Dry Run stop before entering candidate data. CAPTCHA and unknown/unsupported pages remain holds; earlier pages may already have persisted data, disclosed in the updated Full Autopilot consent.
- Verified a synthetic HTTPS/Chromium employer flow using a weak archived template: job-specific CV generation, archive checksum, exact uploaded bytes, one final POST, and same-origin receipt. Independently rendered one- and two-page CVs from synthetic profile data; extracted text validation passed at 100%, and visual inspection found no clipping or broken page boundaries. This is layout QA, not employer or candidate evidence.
- Current full suite: `py -m pytest -q` → 488 passed, one upstream Starlette/httpx deprecation warning. `py -m compileall -q sampoagent tests`, `py -m sampoagent --help`, and `git diff --check` passed. No live employer, candidate database, OAuth account, mailbox, or real application was used.
- The multi-step feature is on `codex/browser-egress-pinning`; README now describes the restricted path and does not imply ATS certification. Implementation-plan and release gates remain open for employer-controlled ATS staging, supported-platform OS egress/Chromium direct-fallback verification, supervised privacy/redaction review, and a supervised pilot. Universal Full Autopilot is not certified.

### CAPTCHA handoff origin hardening — 2026-09-30

- Code inspection found that a CAPTCHA detected after a browser redirect could persist an unrelated destination as the user-facing queue link; same-origin open-redirect paths were also possible. CAPTCHA tasks now always use the reviewed job URL, including legacy UI projection and manual submission evidence; if that URL is no longer safe, the UI hides the link.
- Regression tests reproduced cross-origin/private, sensitive-query and same-origin open-redirect behavior, plus legacy task rows. A follow-up reviewer also identified CAPTCHA results carrying session URLs being marked unverified by the outer runner and manual CAPTCHA evidence mislabeling the starting URL as a confirmation; tests were added for regular and multi-step runners, accurate evidence display, and completing tasks when the current link is unsafe. CAPTCHA-result origin validation now branches only for the challenge; all non-CAPTCHA results retain their origin check. Manual evidence records no confirmation URL. Final verification: `py -m pytest -q` → 514 passed (one existing Starlette/httpx deprecation warning); `py -m compileall -q sampoagent tests`, CLI help and `git diff --check` passed.
- Remaining release gates are employer-controlled ATS staging E2E, supported-platform OS-level egress/direct-fallback proof, supervised privacy review, and a supervised pilot.
- Chromium failure-path evidence (2026-09-30): on Windows with Playwright Chromium 149.0.7827.55, the real pinned-proxy configuration was tested against an upstream CONNECT failure and then a closed proxy while a synthetic HTTPS hostname mapped to loopback. Both navigations failed without a direct fake-employer request; resolver/connector assertions verified the first attempt used the proxy. This closes only that one local Chromium build/path, not OS firewall/effectiveness, other supported platforms, user-controlled ATS staging or the pilot. Focused proxy/adapter/application suite: 86 passed; whole suite: `py -m pytest -q` → 522 passed with one existing Starlette/httpx deprecation warning; compileall, CLI help and `git diff --check` passed.

### Local outcome-learning correction — 2026-09-30

- Candidate-confirmed interview, assessment, offer, rejection or no-response outcomes now affect local role ranking and exact-CV-checksum tie-breaking immediately through the existing Beta-prior-shrunk score. CV adjustments remain restricted to documents already meeting hard requirements and within five fit points of the top match; receipts, CAPTCHA holds and unknown/unconfirmed states remain excluded. The separate Codex opt-in digest retains its five-outcome cohort suppression.
- The analytics summary now excludes application-received receipts so it reports hiring outcomes only. Regression tests cover one-outcome local learning, early CV signal, and receipt exclusion from both local scoring and summaries.
- Verification: `py -m pytest -q` → 524 passed, one existing upstream Starlette/httpx deprecation warning; `py -m compileall -q sampoagent tests`, `py -m sampoagent --help`, and `git diff --check` passed. No candidate database or employer application was accessed. Existing release gates remain open: employer-controlled ATS staging, supported-platform OS-level egress/direct-fallback proof, supervised privacy review, and a supervised pilot.

### Browser-result privacy hardening — 2026-09-30

- Synthetic diagnostics showed a provider-supplied `SubmissionResult.message` could persist arbitrary email, phone, token and local-path text into a candidate-visible timeline or automated receipt. The workflow now records a fixed manual-review note and fixed generic receipt text; only a short reference-shaped ID can be stored from an automated adapter.
- Regression tests first reproduced both leaks, then passed after the fix. Focused application/privacy suite: 78 passed; full suite: `py -m pytest -q` → 526 passed, each with the one existing Starlette/httpx deprecation warning. `py -m compileall -q sampoagent tests`, `py -m sampoagent --help`, and `git diff --check` passed. This is a code-level synthetic check, not the separately required supervised audit of historical local data or browser-driver diagnostics.
