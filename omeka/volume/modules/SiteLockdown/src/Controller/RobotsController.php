<?php declare(strict_types=1);

namespace SiteLockdown\Controller;

use Laminas\Mvc\Controller\AbstractActionController;

class RobotsController extends AbstractActionController
{
    public function indexAction()
    {
        $response = $this->getResponse();
        $response->getHeaders()->addHeaderLine('Content-Type', 'text/plain; charset=UTF-8');
        $response->setContent(<<<'TXT'
# catalog.jonsarkin.com robots.txt
# Strategy: Allow search engine indexing for discoverability.
# Block AI/ML training crawlers to protect artwork images.
# Updated: March 2026. Review quarterly for new crawlers.

# AI/ML Training Crawlers - Do Not Scrape
User-agent: GPTBot
Disallow: /

User-agent: ChatGPT-User
Disallow: /

User-agent: CCBot
Disallow: /

User-agent: Google-Extended
Disallow: /

User-agent: anthropic-ai
Disallow: /

User-agent: ClaudeBot
Disallow: /

User-agent: Bytespider
Disallow: /

User-agent: Diffbot
Disallow: /

User-agent: FacebookBot
Disallow: /

User-agent: Applebot-Extended
Disallow: /

User-agent: PerplexityBot
Disallow: /

User-agent: Cohere-ai
Disallow: /

# Standard search engine crawlers - ALLOWED (we want to be indexed)
User-agent: Googlebot
Allow: /

User-agent: Bingbot
Allow: /

User-agent: *
Allow: /

TXT);
        return $response;
    }
}
