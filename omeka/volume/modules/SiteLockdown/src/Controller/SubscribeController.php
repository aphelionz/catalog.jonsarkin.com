<?php declare(strict_types=1);

namespace SiteLockdown\Controller;

use Laminas\Http\Client;
use Laminas\Mvc\Controller\AbstractActionController;

class SubscribeController extends AbstractActionController
{
    private Client $httpClient;
    private array $config;

    public function __construct(Client $httpClient, array $config)
    {
        $this->httpClient = $httpClient;
        $this->config = $config;
    }

    public function indexAction()
    {
        $request = $this->getRequest();
        if (!$request->isPost()) {
            return $this->redirect()->toUrl('/');
        }
        $post = $request->getPost()->toArray();

        // Honeypot — filled means bot; pretend success, drop silently
        if (!empty($post['website'] ?? '')) {
            return $this->redirect()->toUrl('/?signup=ok');
        }

        $email = trim($post['email'] ?? '');
        if (!filter_var($email, FILTER_VALIDATE_EMAIL) || strlen($email) > 254) {
            return $this->redirect()->toUrl('/?signup=invalid');
        }

        try {
            $this->subscribeShopify($email);
            return $this->redirect()->toUrl('/?signup=ok');
        } catch (\Throwable $e) {
            error_log('[prelaunch-signup] ' . $e->getMessage());
            return $this->redirect()->toUrl('/?signup=error');
        }
    }

    private function subscribeShopify(string $email): void
    {
        $tag = $this->config['signup_tag'] ?? 'prelaunch-signup';

        // 1) Try customerCreate (new subscriber path)
        $createRes = $this->graphql(
            'mutation C($i: CustomerInput!) { customerCreate(input: $i) { customer { id } userErrors { field message } } }',
            ['i' => [
                'email' => $email,
                'emailMarketingConsent' => [
                    'marketingState' => 'SUBSCRIBED',
                    'marketingOptInLevel' => 'SINGLE_OPT_IN',
                ],
                'tags' => [$tag],
            ]]
        );
        $userErrors = $createRes['data']['customerCreate']['userErrors'] ?? [];
        $alreadyExists = (bool) array_filter(
            $userErrors,
            function ($e) {
                $msg = $e['message'] ?? '';
                return stripos($msg, 'has already been taken') !== false
                    || stripos($msg, 'already') !== false;
            }
        );
        if (!$alreadyExists) {
            if ($userErrors) {
                throw new \RuntimeException('customerCreate: ' . json_encode($userErrors));
            }
            return;
        }

        // 2) Existing customer — look up by email, update consent, add tag
        $lookup = $this->graphql(
            'query L($q: String!) { customers(first: 1, query: $q) { edges { node { id } } } }',
            ['q' => 'email:' . $email]
        );
        $id = $lookup['data']['customers']['edges'][0]['node']['id'] ?? null;
        if (!$id) {
            throw new \RuntimeException('customerCreate said exists but lookup failed for ' . $email);
        }

        $update = $this->graphql(
            'mutation U($i: CustomerEmailMarketingConsentUpdateInput!) { customerEmailMarketingConsentUpdate(input: $i) { userErrors { field message } } }',
            ['i' => [
                'customerId' => $id,
                'emailMarketingConsent' => [
                    'marketingState' => 'SUBSCRIBED',
                    'marketingOptInLevel' => 'SINGLE_OPT_IN',
                ],
            ]]
        );
        if (!empty($update['data']['customerEmailMarketingConsentUpdate']['userErrors'])) {
            throw new \RuntimeException('consentUpdate: ' . json_encode($update['data']['customerEmailMarketingConsentUpdate']['userErrors']));
        }

        $this->graphql(
            'mutation T($id: ID!, $tags: [String!]!) { tagsAdd(id: $id, tags: $tags) { userErrors { field message } } }',
            ['id' => $id, 'tags' => [$tag]]
        );
    }

    private function graphql(string $query, array $variables): array
    {
        $token = $this->config['shopify_admin_api_token'] ?? '';
        if ($token === '') {
            throw new \RuntimeException('SHOPIFY_ADMIN_API_TOKEN is not configured');
        }

        $client = clone $this->httpClient;
        $client->resetParameters(true);
        $client->setUri(sprintf(
            'https://%s/admin/api/%s/graphql.json',
            $this->config['shopify_shop_domain'] ?? 'jonsarkin.myshopify.com',
            $this->config['shopify_api_version'] ?? '2025-01'
        ));
        $client->setMethod('POST');
        $client->setHeaders([
            'X-Shopify-Access-Token' => $token,
            'Content-Type' => 'application/json',
        ]);
        $client->setRawBody(json_encode(['query' => $query, 'variables' => $variables]));
        $client->setOptions(['timeout' => 10]);
        $response = $client->send();
        if (!$response->isSuccess()) {
            throw new \RuntimeException('Shopify HTTP ' . $response->getStatusCode() . ': ' . substr($response->getBody(), 0, 500));
        }
        return json_decode($response->getBody(), true) ?: [];
    }
}
