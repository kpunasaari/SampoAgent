"""Persistent first-run progression shared by the UI and worker gate."""
from sampoagent.candidate.questions import SECTIONS
from sampoagent.db.repository import Repository

STEPS = (*SECTIONS, 'review', 'ready')


def pending(repository: Repository) -> bool:
    return repository.setting('guided_onboarding') == 'true' and repository.setting('setup_finished') != 'true'


def step_index(repository: Repository) -> int:
    try:
        return max(0, min(int(repository.setting('setup_step') or '0'), len(STEPS) - 1))
    except ValueError:
        return 0


def current_url(repository: Repository) -> str:
    step = STEPS[step_index(repository)]
    return '/onboarding/ready' if step == 'ready' else '/onboarding?section=' + step


def allowed(repository: Repository, path: str, section: str | None) -> bool:
    index = step_index(repository)
    if path in {'/onboarding', '/landing'}:
        return section is None or section not in STEPS or STEPS.index(section) <= index
    if path.startswith('/onboarding/questions/'):
        key = path.rsplit('/', 1)[-1]
        return key not in SECTIONS or STEPS.index(key) <= index
    if path in {'/onboarding/confirm', '/onboarding/review-complete'}:
        return index >= STEPS.index('review')
    if path == '/onboarding/ready':
        return index == STEPS.index('ready')
    if path == '/profile' or path.startswith('/profile/') or path == '/cvs' or path.startswith('/cvs/'):
        return index >= STEPS.index('review')
    if path == '/careers' or path.startswith('/careers/'):
        return index == STEPS.index('ready')
    return False
