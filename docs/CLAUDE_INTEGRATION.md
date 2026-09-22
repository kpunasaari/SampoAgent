# Claude integration

Claude adapters should implement the provider-neutral `BrowserAgent` protocol. They may inspect and prepare forms but must return manual action for CAPTCHA, authentication, or unknown high-risk questions. Reuse the focused skills under `.agents/skills` and pass only normalized job data plus confirmed relevant candidate facts.
