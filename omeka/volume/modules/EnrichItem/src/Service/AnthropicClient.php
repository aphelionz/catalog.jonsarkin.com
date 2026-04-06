<?php declare(strict_types=1);

namespace EnrichItem\Service;

use Laminas\Http\Client as HttpClient;

/**
 * Anthropic API client for field-level artwork enrichment.
 *
 * Downloads an artwork image, resizes it, sends it to Claude with dynamic
 * per-field instructions, and returns the enrichment result with usage/cost info.
 */
class AnthropicClient
{
    private const IMAGE_MAX_DIM = 1024;
    private const IMAGE_QUALITY = 85;
    private const API_URL = 'https://api.anthropic.com/v1/messages';
    private const API_VERSION = '2023-06-01';
    private const MAX_TOKENS = 4096;

    public const MODEL_MAP = [
        'haiku'  => 'claude-haiku-4-5-20251001',
        'sonnet' => 'claude-sonnet-4-6',
        'opus'   => 'claude-opus-4-6',
    ];

    // Per-million-token pricing (USD)
    private const PRICING = [
        'claude-haiku-4-5-20251001' => ['input' => 0.80,  'output' => 4.00],
        'claude-sonnet-4-6'         => ['input' => 3.00,  'output' => 15.00],
        'claude-opus-4-6'           => ['input' => 15.00, 'output' => 75.00],
    ];

    private string $apiKey;
    private string $defaultModel;
    private HttpClient $httpClient;
    private $logger;

    public function __construct(
        string $apiKey,
        string $defaultModel,
        HttpClient $httpClient,
        $logger
    ) {
        $this->apiKey = $apiKey;
        $this->defaultModel = $defaultModel;
        $this->httpClient = $httpClient;
        $this->logger = $logger;
    }

    /**
     * Enrich a single field for an artwork image.
     *
     * @param string $imageUrl  URL of the artwork image (internal Docker URL)
     * @param string $systemPrompt  Dynamic system prompt with instructions + vocab constraints
     * @param string $userPrompt  Short trigger prompt (e.g. "Analyze this artwork...")
     * @param string|null $model  Model short name (haiku/sonnet/opus)
     * @return array{value: string, usage: array{input_tokens: int, output_tokens: int, model: string, cost_usd: float}}
     */
    public function enrichField(string $imageUrl, string $systemPrompt, string $userPrompt, ?string $model = null): array
    {
        if (!$this->apiKey) {
            throw new \RuntimeException('ANTHROPIC_API_KEY environment variable is not set');
        }

        $model = $model ?? $this->defaultModel;
        $modelId = self::MODEL_MAP[$model] ?? $model;

        [$b64Image, $mediaType] = $this->downloadAndEncodeImage($imageUrl);

        $requestBody = [
            'model' => $modelId,
            'max_tokens' => self::MAX_TOKENS,
            'system' => $systemPrompt,
            'messages' => [
                [
                    'role' => 'user',
                    'content' => [
                        [
                            'type' => 'image',
                            'source' => [
                                'type' => 'base64',
                                'media_type' => $mediaType,
                                'data' => $b64Image,
                            ],
                        ],
                        [
                            'type' => 'text',
                            'text' => $userPrompt,
                        ],
                    ],
                ],
            ],
        ];

        $client = clone $this->httpClient;
        $client->resetParameters(true);
        $client->setUri(self::API_URL);
        $client->setMethod('POST');
        $client->setHeaders([
            'Content-Type' => 'application/json',
            'x-api-key' => $this->apiKey,
            'anthropic-version' => self::API_VERSION,
        ]);
        $client->setRawBody(json_encode($requestBody));
        $client->setOptions(['timeout' => 120]);

        $response = $client->send();
        if (!$response->isSuccess()) {
            $body = json_decode($response->getBody(), true);
            $detail = $body['error']['message'] ?? $response->getBody();
            throw new \RuntimeException(sprintf(
                'Anthropic API error (HTTP %d): %s',
                $response->getStatusCode(),
                $detail
            ));
        }

        $apiResponse = json_decode($response->getBody(), true);
        $rawText = trim($apiResponse['content'][0]['text'] ?? '');

        // Strip markdown fences if present
        if (str_starts_with($rawText, '```')) {
            $lines = explode("\n", $rawText);
            $lines = array_filter($lines, fn($l) => !str_starts_with(trim($l), '```'));
            $rawText = trim(implode("\n", $lines));
        }

        // Treat "NULL" response as empty
        if (strtoupper($rawText) === 'NULL') {
            $rawText = '';
        }

        $usage = $apiResponse['usage'] ?? [];
        $inputTokens = $usage['input_tokens'] ?? 0;
        $outputTokens = $usage['output_tokens'] ?? 0;

        return [
            'value' => $rawText,
            'usage' => [
                'input_tokens' => $inputTokens,
                'output_tokens' => $outputTokens,
                'model' => $modelId,
                'cost_usd' => $this->estimateCost($modelId, $inputTokens, $outputTokens),
            ],
        ];
    }

    /**
     * Build the system prompt for a field enrichment call.
     *
     * @param string $fieldLabel  Human-readable field name
     * @param string $instructions  User-written instructions
     * @param array|null $vocabTerms  Controlled vocabulary terms, if any
     * @return string
     */
    public static function buildSystemPrompt(string $fieldLabel, string $instructions, ?array $vocabTerms = null): string
    {
        $parts = [
            "You are cataloging artworks by Jon Sarkin (1953-2024) for a catalog raisonne.",
            "",
            "Your task is to determine the value for the field: \"{$fieldLabel}\".",
            "",
            $instructions,
        ];

        if (!empty($vocabTerms)) {
            $termList = implode("\n- ", $vocabTerms);
            $parts[] = "";
            $parts[] = "IMPORTANT: Your response MUST be exactly one of these allowed values:";
            $parts[] = "- " . $termList;
            $parts[] = "Do not invent new values. Pick the closest match from this list.";
        }

        $parts[] = "";
        $parts[] = "Return ONLY the value. No JSON wrapping, no markdown, no explanation.";
        $parts[] = "If you cannot determine the value, return the single word: NULL";

        return implode("\n", $parts);
    }

    /**
     * Send a multi-turn messages request (e.g. for few-shot prompting).
     *
     * @param string $systemPrompt  System prompt text
     * @param array $messages  Pre-built messages array [{role, content}, ...]
     * @param string|null $model  Model short name (haiku/sonnet/opus)
     * @return array{value: string, usage: array{input_tokens: int, output_tokens: int, model: string, cost_usd: float}}
     */
    public function sendMessages(string $systemPrompt, array $messages, ?string $model = null): array
    {
        if (!$this->apiKey) {
            throw new \RuntimeException('ANTHROPIC_API_KEY environment variable is not set');
        }

        $model = $model ?? $this->defaultModel;
        $modelId = self::MODEL_MAP[$model] ?? $model;

        $requestBody = [
            'model' => $modelId,
            'max_tokens' => self::MAX_TOKENS,
            'system' => $systemPrompt,
            'messages' => $messages,
        ];

        $client = clone $this->httpClient;
        $client->resetParameters(true);
        $client->setUri(self::API_URL);
        $client->setMethod('POST');
        $client->setHeaders([
            'Content-Type' => 'application/json',
            'x-api-key' => $this->apiKey,
            'anthropic-version' => self::API_VERSION,
        ]);
        $client->setRawBody(json_encode($requestBody));
        $client->setOptions(['timeout' => 120]);

        $response = $client->send();
        if (!$response->isSuccess()) {
            $body = json_decode($response->getBody(), true);
            $detail = $body['error']['message'] ?? $response->getBody();
            throw new \RuntimeException(sprintf(
                'Anthropic API error (HTTP %d): %s',
                $response->getStatusCode(),
                $detail
            ));
        }

        $apiResponse = json_decode($response->getBody(), true);
        $rawText = trim($apiResponse['content'][0]['text'] ?? '');

        if (str_starts_with($rawText, '```')) {
            $lines = explode("\n", $rawText);
            $lines = array_filter($lines, fn($l) => !str_starts_with(trim($l), '```'));
            $rawText = trim(implode("\n", $lines));
        }

        if (strtoupper($rawText) === 'NULL') {
            $rawText = '';
        }

        $usage = $apiResponse['usage'] ?? [];
        $inputTokens = $usage['input_tokens'] ?? 0;
        $outputTokens = $usage['output_tokens'] ?? 0;

        return [
            'value' => $rawText,
            'usage' => [
                'input_tokens' => $inputTokens,
                'output_tokens' => $outputTokens,
                'model' => $modelId,
                'cost_usd' => $this->estimateCost($modelId, $inputTokens, $outputTokens),
            ],
        ];
    }

    /**
     * Download image, resize to max dimension, return [base64, media_type].
     *
     * @param int $maxDim  Maximum dimension (default 1024, use 512 for few-shot examples)
     * @return array{0: string, 1: string}
     */
    public function downloadAndEncodeImage(string $imageUrl, int $maxDim = self::IMAGE_MAX_DIM): array
    {
        $ch = curl_init($imageUrl);
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => 60,
            CURLOPT_FOLLOWLOCATION => true,
        ]);
        $imageData = curl_exec($ch);
        $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $error = curl_error($ch);
        curl_close($ch);

        if ($imageData === false || $httpCode !== 200) {
            throw new \RuntimeException(sprintf(
                'Failed to download image (HTTP %d%s): %s',
                $httpCode,
                $error ? ", $error" : '',
                $imageUrl
            ));
        }
        $img = @imagecreatefromstring($imageData);
        if ($img === false) {
            throw new \RuntimeException('Failed to decode image: ' . $imageUrl);
        }

        $w = imagesx($img);
        $h = imagesy($img);

        if (max($w, $h) > $maxDim) {
            $scale = $maxDim / max($w, $h);
            $newW = (int) ($w * $scale);
            $newH = (int) ($h * $scale);
            $resized = imagecreatetruecolor($newW, $newH);
            imagecopyresampled($resized, $img, 0, 0, 0, 0, $newW, $newH, $w, $h);
            imagedestroy($img);
            $img = $resized;
        }

        ob_start();
        imagejpeg($img, null, self::IMAGE_QUALITY);
        $jpegData = ob_get_clean();
        imagedestroy($img);

        return [base64_encode($jpegData), 'image/jpeg'];
    }

    public function estimateCost(string $modelId, int $inputTokens, int $outputTokens): float
    {
        $prices = self::PRICING[$modelId] ?? ['input' => 3.0, 'output' => 15.0];
        return round(
            $inputTokens * $prices['input'] / 1_000_000
            + $outputTokens * $prices['output'] / 1_000_000,
            6
        );
    }

    public function getApiKey(): string
    {
        return $this->apiKey;
    }
}
