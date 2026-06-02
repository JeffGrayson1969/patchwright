# Crawler & Bot Blocking Policy — aegisq.ai

> **Version:** 1.0.0
> **Date:** 2026-04-01
> **Owner:** Team India (DevOps)
> **Status:** MANDATORY — All aegisq.ai subdomains

## Purpose

All AegisQ infrastructure under the `aegisq.ai` domain serves enterprise security customers. Exposing any content to search engines or AI training crawlers creates:
- **Enumeration risk** — attackers discover API endpoints, service versions, infrastructure layout
- **Customer data risk** — cached responses may contain tenant-identifying information
- **Competitive intelligence leak** — platform capabilities exposed to competitors
- **Attack surface expansion** — indexed endpoints become targets for automated scanners

## Policy: BLOCK ALL CRAWLERS ON ALL SUBDOMAINS

No exceptions. This applies to staging, production, and any future subdomains.

## Protected Subdomains

| Subdomain | Product | Environment |
|-----------|---------|-------------|
| api.aegisq.ai | AegisQ Security | Production |
| app.aegisq.ai | AegisQ Security / AI Sentinel | Production |
| status.aegisq.ai | AegisQ Security | Production |
| license.aegisq.ai | License Server | Production |
| portal.aegisq.ai | Internal Portal | Production |
| helm.aegisq.ai | AegisQ Helm | Production |
| api.helm.aegisq.ai | AegisQ Helm API | Production |
| *.staging.aegisq.ai | All Products | Staging |

**Note:** `aegisq.com` is the marketing site and IS indexed — that's intentional. Only `aegisq.ai` is blocked.

## 5-Layer Defense Implementation

### Layer 1: robots.txt (Application Level)
Every HTTP-serving application must serve this at `/robots.txt`:
```
User-agent: *
Disallow: /

# Search engines
User-agent: Googlebot
Disallow: /
User-agent: Bingbot
Disallow: /
User-agent: Yandexbot
Disallow: /
User-agent: Baiduspider
Disallow: /
User-agent: DuckDuckBot
Disallow: /

# AI crawlers
User-agent: GPTBot
Disallow: /
User-agent: ClaudeBot
Disallow: /
User-agent: Google-Extended
Disallow: /
User-agent: CCBot
Disallow: /
User-agent: PerplexityBot
Disallow: /
User-agent: Bytespider
Disallow: /
User-agent: Applebot-Extended
Disallow: /
User-agent: anthropic-ai
Disallow: /
User-agent: cohere-ai
Disallow: /
User-agent: AmazonBot
Disallow: /
User-agent: Meta-ExternalAgent
Disallow: /
User-agent: FacebookBot
Disallow: /

# SEO tools
User-agent: AhrefsBot
Disallow: /
User-agent: SemrushBot
Disallow: /
User-agent: DotBot
Disallow: /
User-agent: MJ12bot
Disallow: /
User-agent: rogerbot
Disallow: /
```

### Layer 2: HTTP Response Headers (All Responses)
Every response from every service must include:
```
X-Robots-Tag: noindex, nofollow, noarchive, nosnippet, noimageindex, notranslate
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
Strict-Transport-Security: max-age=63072000; includeSubDomains; preload
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: camera=(), microphone=(), geolocation=()
```

### Layer 3: HTML Meta Tags (Dashboard/UI Pages)
All rendered HTML pages must include in `<head>`:
```html
<meta name="robots" content="noindex, nofollow, noarchive, nosnippet">
<meta name="googlebot" content="noindex, nofollow">
```

### Layer 4: Cloud Armor WAF Rules (Network Edge)
Block at the load balancer before requests reach pods:
```
# Rule 1 (Priority 1000): Block search engine crawlers
Condition: request.headers['user-agent'].matches('(?i)(googlebot|bingbot|yandexbot|baiduspider|duckduckbot|slurp|sogou|exabot|ia_archiver|facebot)')
Action: deny(403)

# Rule 2 (Priority 1001): Block AI training crawlers
Condition: request.headers['user-agent'].matches('(?i)(gptbot|chatgpt|claude-web|claudebot|google-extended|ccbot|perplexitybot|bytespider|applebot-extended|anthropic-ai|cohere-ai|amazonbot|meta-externalagent|facebookbot)')
Action: deny(403)

# Rule 3 (Priority 1002): Block SEO tools
Condition: request.headers['user-agent'].matches('(?i)(ahrefs|semrush|majestic|moz|dotbot|rogerbot|screaming.frog|sitebulb|deepcrawl|mj12bot)')
Action: deny(403)

# Rule 4 (Priority 1003): Block empty/suspicious User-Agents
Condition: request.headers['user-agent'].size() < 10
Action: deny(403)
```

### Layer 5: DNS Hardening
- No wildcard A/AAAA records — each subdomain is explicit
- DNSSEC enabled on aegisq.ai zone
- No TXT records that leak internal information
- Certificate Transparency log monitoring enabled
- No zone transfer (AXFR) allowed

## Verification Procedures

### Automated (CI/CD — Every Deployment)
The CI/CD pipeline MUST run these checks after every deployment:
```bash
#!/bin/bash
DOMAIN=$1

# 1. robots.txt exists and blocks all
ROBOTS=$(curl -sf "https://${DOMAIN}/robots.txt")
echo "$ROBOTS" | grep -q "Disallow: /" || { echo "FAIL: robots.txt"; exit 1; }

# 2. X-Robots-Tag header present
curl -sI "https://${DOMAIN}/health" | grep -qi "x-robots-tag.*noindex" || { echo "FAIL: X-Robots-Tag"; exit 1; }

# 3. Security headers present
curl -sI "https://${DOMAIN}/health" | grep -qi "x-frame-options" || { echo "FAIL: X-Frame-Options"; exit 1; }
curl -sI "https://${DOMAIN}/health" | grep -qi "strict-transport-security" || { echo "FAIL: HSTS"; exit 1; }

# 4. Bot UA blocked (may not work without Cloud Armor)
STATUS=$(curl -sI -o /dev/null -w "%{http_code}" -A "Googlebot/2.1" "https://${DOMAIN}/health")
[ "$STATUS" = "403" ] && echo "PASS: Bot UA blocked" || echo "WARN: Bot UA not blocked at edge"

echo "PASS: Crawler blocking verified for ${DOMAIN}"
```

### Manual (Monthly — DevOps PM)
First Monday of every month:
1. Run automated checks against ALL subdomains
2. Check Google Search Console for any indexed aegisq.ai pages
3. Review Cloud Armor logs for new bot User-Agents not in deny list
4. Review Certificate Transparency logs for unexpected aegisq.ai certificates
5. Document results in Notion "Crawler Blocking Status" page

## Incident Response: Crawler Detected

| Step | Action | Owner | Timeline |
|------|--------|-------|----------|
| 1 | Identify crawler in access logs | India CI/CD | Immediate |
| 2 | Add User-Agent to Cloud Armor deny list | India Infra | Same hour |
| 3 | Check if content was cached/indexed | DevOps PM | Same day |
| 4 | Submit removal request if indexed | DevOps PM | Same day |
| 5 | File Linear issue (P1 security) | DevOps PM | Same day |
| 6 | Update this policy with new bot name | India Lead | Within 1 week |
| 7 | Update Notion crawler blocking status | DevOps PM | Same day |

## Exceptions

**There are no exceptions.** If a legitimate need arises to expose any aegisq.ai subdomain to crawlers (e.g., a public documentation site), it must:
1. Be approved by Jeff in writing
2. Use a separate, dedicated subdomain (e.g., docs.aegisq.ai)
3. Contain NO customer data, API endpoints, or infrastructure details
4. Be reviewed by Echo team for information leakage
5. Be documented in this policy as an explicit exception

## Compliance

This policy supports:
- **SOC 2 CC6.1** — Logical access security
- **ISO 27001 A.13** — Network security management
- **GDPR Article 25** — Data protection by design
- **NIST 800-53 SC-7** — Boundary protection
