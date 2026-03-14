<?php declare(strict_types=1);

namespace EnrichItem\Service;

use Laminas\Http\Client as HttpClient;

/**
 * Direct Anthropic API client for artwork enrichment.
 *
 * Downloads an artwork image, resizes it, sends it to Claude for structured
 * analysis, validates the response against controlled vocabularies, and
 * returns the enrichment result with usage/cost info.
 */
class AnthropicClient
{
    public const PROMPT_VERSION = 4;

    private const IMAGE_MAX_DIM = 1024;
    private const IMAGE_QUALITY = 85;
    private const API_URL = 'https://api.anthropic.com/v1/messages';
    private const API_VERSION = '2023-06-01';
    private const MAX_TOKENS = 4096;

    public const WORK_TYPES = [
        'Drawing', 'Painting', 'Collage', 'Mixed Media',
        'Sculpture', 'Print', 'Other',
    ];

    public const SUPPORTS = [
        'Paper', 'Cardboard', 'Cardboard album sleeve', 'Canvas', 'Board', 'Wood',
        'Found Object', 'Envelope', 'Album Sleeve', 'Other',
    ];

    public const MOTIFS = [
        'Eyes', 'Fish', 'Faces', 'Hands', 'Text Fragments',
        'Grids', 'Circles', 'Patterns', 'Animals', 'Names/Words',
        'Maps', 'Numbers',
    ];

    public const CONDITIONS = ['Excellent', 'Good', 'Fair', 'Poor', 'Not Examined'];

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

    public const ANALYSIS_PROMPT = <<<'PROMPT'
You are cataloging artworks by Jon Sarkin (1953–2024) for a catalog raisonné.
Analyze this artwork image and return a JSON object with the following fields.
{
  "transcription": "Transcribe ALL visible text exactly as written by the artist.
                     Do not correct spelling, grammar, punctuation, or capitalization.
                     Do not normalize or interpret — reproduce what is on the surface.
                     Include all words, phrases, letter sequences, and isolated characters.
                     Transcribe every instance of repeated sequences individually
                     (e.g., 'eee eee eee eee eee' not 'eee ×5').
                     Include text fragments, cultural references, and symbols that
                     function as text (describe symbols in brackets: [circle with cross]).
                     Do NOT include the artist's signature or date — these are captured
                     in separate fields. The signature is usually 'JMS' followed by a
                     two-digit year, typically in the lower right.
                     Organize spatially: top to bottom, left to right. Use line breaks
                     to separate distinct text areas.
                     Use [illegible] for unreadable portions.
                     Return null if no text is visible.",
  "signature": "Return a SINGLE character indicating where the signature appears.
                Must be exactly one of: ↖ ↑ ↗ ← → ↙ ↓ ↘ ∅
                Use ∅ if unsigned or no signature visible.
                Return ONLY the one arrow character or ∅ — no other text.",
  "date": "Year the work was created, if determinable from the signature or
           text in the artwork. Return as a string: '2005', 'c. 2005', etc.
           Return null if not determinable.",
  "medium": "Materials/media ONLY — do NOT include the support surface.
             Examples: 'Marker', 'Ink and marker', 'Acrylic and collage',
             'Mixed media', 'Graphite', 'Oil paint'.
             Return null if uncertain.",
  "support": "The surface/substrate. Must be one of:
              Cardboard album sleeve, Paper, Canvas, Board, Wood,
              Found Object, Envelope, Other.
              If the work is square (approximately 12.5 × 12.5 inches),
              the support is almost certainly 'Cardboard album sleeve.'
              Do not override to 'Paper' or 'Cardboard' unless clearly
              not an album sleeve.
              Return null if uncertain.",
  "work_type": "Must be one of: Drawing, Painting, Collage, Mixed Media,
                Sculpture, Print, Other. Return null if uncertain.",
  "motifs": ["Visual motifs present. Choose from: Eyes, Fish, Faces, Hands,
              Text Fragments, Grids, Circles, Patterns, Animals, Names/Words,
              Maps, Numbers, Desert, Boats, Creatures.
              ERR ON THE SIDE OF INCLUSION. If a motif is arguably present,
              include it. This field is additive — more tags are better than
              fewer tags.
              Return empty array if none match."],
  "condition_notes": "Brief note on visible condition issues (tears, staining,
                      foxing, fading). When describing condition, note that
                      edge wear, tearing, creasing, and staining are typically
                      inherent to the artist's process, not post-creation damage.
                      Sarkin did not treat his works as precious objects. Use the
                      phrase 'inherent to the artist's process' to distinguish
                      process-related wear from external damage. Return null if
                      the work appears to be in good condition."
}
Return ONLY valid JSON. No markdown fences, no explanation.
PROMPT;

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
     * Analyze an artwork image via Claude.
     *
     * @return array Enrichment fields + 'usage' key with token counts and cost
     */
    public function analyze(string $imageUrl, ?string $model = null): array
    {
        $model = $model ?? $this->defaultModel;
        $modelId = self::MODEL_MAP[$model] ?? $model;

        [$b64Image, $mediaType] = $this->downloadAndEncodeImage($imageUrl);

        $requestBody = [
            'model' => $modelId,
            'max_tokens' => self::MAX_TOKENS,
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
                            'text' => self::ANALYSIS_PROMPT,
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
        $rawText = $apiResponse['content'][0]['text'] ?? '';
        $parsed = $this->parseResponse($rawText);
        $result = $this->validateEnrichment($parsed);

        // Attach usage info
        $usage = $apiResponse['usage'] ?? [];
        $inputTokens = $usage['input_tokens'] ?? 0;
        $outputTokens = $usage['output_tokens'] ?? 0;
        $result['usage'] = [
            'input_tokens' => $inputTokens,
            'output_tokens' => $outputTokens,
            'model' => $modelId,
            'cost_usd' => $this->estimateCost($modelId, $inputTokens, $outputTokens),
        ];

        return $result;
    }

    /**
     * Download image, resize to max 1024px, return [base64, media_type].
     *
     * @return array{0: string, 1: string}
     */
    public function downloadAndEncodeImage(string $imageUrl): array
    {
        $client = clone $this->httpClient;
        $client->resetParameters(true);
        $client->setUri($imageUrl);
        $client->setMethod('GET');
        $client->setOptions(['timeout' => 60]);

        $response = $client->send();
        if (!$response->isSuccess()) {
            throw new \RuntimeException(sprintf(
                'Failed to download image (HTTP %d): %s',
                $response->getStatusCode(),
                $imageUrl
            ));
        }

        $imageData = $response->getBody();
        $img = @imagecreatefromstring($imageData);
        if ($img === false) {
            throw new \RuntimeException('Failed to decode image: ' . $imageUrl);
        }

        $w = imagesx($img);
        $h = imagesy($img);

        if (max($w, $h) > self::IMAGE_MAX_DIM) {
            $scale = self::IMAGE_MAX_DIM / max($w, $h);
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

    public function parseResponse(string $rawText): array
    {
        $rawText = trim($rawText);

        // Strip markdown fences
        if (str_starts_with($rawText, '```')) {
            $lines = explode("\n", $rawText);
            $lines = array_filter($lines, fn($l) => !str_starts_with(trim($l), '```'));
            $rawText = implode("\n", $lines);
        }

        $data = json_decode($rawText, true);
        if (json_last_error() === JSON_ERROR_NONE && is_array($data)) {
            return $data;
        }

        // Attempt to repair truncated JSON
        $repaired = $this->repairTruncatedJson($rawText);
        if ($repaired !== null) {
            $this->logger->warn('EnrichItem: repaired truncated JSON (max_tokens likely hit)');
            return $repaired;
        }

        $this->logger->warn('EnrichItem: invalid JSON from Claude: ' . substr($rawText, 0, 300));
        return [];
    }

    public function repairTruncatedJson(string $text): ?array
    {
        $text = rtrim($text, '\\');

        // Close any open string
        if (substr_count($text, '"') % 2 === 1) {
            $text .= '"';
        }

        // Balance braces/brackets
        $stack = [];
        $inString = false;
        $escape = false;
        $len = strlen($text);
        for ($i = 0; $i < $len; $i++) {
            $ch = $text[$i];
            if ($escape) {
                $escape = false;
                continue;
            }
            if ($ch === '\\') {
                $escape = true;
                continue;
            }
            if ($ch === '"') {
                $inString = !$inString;
                continue;
            }
            if ($inString) {
                continue;
            }
            if ($ch === '{' || $ch === '[') {
                $stack[] = ($ch === '{') ? '}' : ']';
            } elseif (($ch === '}' || $ch === ']') && !empty($stack)) {
                array_pop($stack);
            }
        }

        $text .= implode('', array_reverse($stack));

        $data = json_decode($text, true);
        return (json_last_error() === JSON_ERROR_NONE && is_array($data)) ? $data : null;
    }

    public function validateEnrichment(array $data): array
    {
        $result = [];

        // String fields — trim or null
        foreach (['transcription', 'signature', 'date', 'medium', 'condition_notes'] as $key) {
            $val = $data[$key] ?? null;
            $result[$key] = ($val !== null && is_string($val)) ? trim($val) : null;
            if ($result[$key] === '') {
                $result[$key] = null;
            }
        }

        // Controlled vocab: work_type
        $workType = $data['work_type'] ?? null;
        $result['work_type'] = in_array($workType, self::WORK_TYPES, true) ? $workType : null;

        // Controlled vocab: support
        $support = $data['support'] ?? null;
        $result['support'] = in_array($support, self::SUPPORTS, true) ? $support : null;

        // Controlled vocab: motifs (filtered list)
        $motifs = $data['motifs'] ?? [];
        $result['motifs'] = is_array($motifs)
            ? array_values(array_filter($motifs, fn($m) => in_array($m, self::MOTIFS, true)))
            : [];

        return $result;
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

    public function getPromptVersion(): int
    {
        return self::PROMPT_VERSION;
    }
}
