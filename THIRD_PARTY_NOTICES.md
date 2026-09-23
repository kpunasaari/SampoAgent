# Third-party notices

## Scrapling

SampoAgent depends on Scrapling by Karim Shoair (https://github.com/D4Vinci/Scrapling), licensed under BSD-3-Clause. Scrapling is an external dependency and is not vendored in this repository. SampoAgent uses its static HTML `Selector` parser for CSS/JSON-LD extraction; the installed package's license and full notices are distributed by the upstream project.

SampoAgent does not invoke Scrapling's stealth, browser, proxy-rotation, TLS-impersonation, or CAPTCHA-related fetch paths. Network access is performed by SampoAgent's bounded source fetcher and remains subject to the target site's robots policy and access rules.
