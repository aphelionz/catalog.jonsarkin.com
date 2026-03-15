# EnrichItem

Claude-powered field-level artwork enrichment for Omeka S. Enrich any metadata field across all items in a resource template using the Anthropic API.

## Features

- **Per-field enrichment**: Select any property from the Artwork resource template, write custom instructions, and enrich all items missing that field
- **Model selection**: Choose between Haiku (fast/cheap), Sonnet (balanced), or Opus (best quality)
- **Controlled vocabulary enforcement**: Fields with a Custom Vocab automatically constrain Claude's output to allowed values
- **Saved instructions**: Instructions are stored per-field and auto-load on selection
- **Preview**: Test enrichment on a single random item before committing to a full run
- **Real-time batch**: Enrich all items via background job (Admin > Jobs)
- **Batch API**: Submit to Anthropic's Batch API for 50% cost savings (async, typically ~1 hour)
- **Force mode**: Re-enrich items that already have a value
- **Per-item cache**: Results cached by (item_id, property_id) to avoid redundant API calls

## Usage

1. Navigate to **Admin > Enrich Fields**
2. Select a field from the dropdown (shows missing count per field)
3. Write instructions for how Claude should populate the field
4. Choose a model
5. Click **Preview (1 item)** to test on a single item
6. Click **Enrich All** for real-time enrichment or **Submit Batch** for 50% cheaper async processing

For batch jobs, check status in the Batch Jobs table at the bottom of the page. Click **Collect** when a batch is complete to apply results.

## Dynamic prompt construction

The system prompt sent to Claude is built at call time from:

1. Base context: "You are cataloging artworks by Jon Sarkin (1953-2024) for a catalog raisonné."
2. Your custom instructions for the field
3. Controlled vocabulary constraint (if the field has a Custom Vocab): "Your response MUST be exactly one of: [term1, term2, ...]"
4. Output format: "Return ONLY the value. No JSON, no markdown, no explanation."

The artwork image is always sent with the prompt.

## Adding new fields

Any property added to the Artwork resource template will appear automatically in the field dropdown on page refresh. If the property uses a Custom Vocab data type, the allowed values will be enforced.

## Configuration

Set in `config/module.config.php` under `enrich_item`:

| Key | Default | Description |
|-----|---------|-------------|
| `resource_template_id` | `2` | Artwork resource template ID |
| `default_model` | `haiku` | Default Claude model |
| `timeout` | `120` | API call timeout (seconds) |

## Requirements

- `ANTHROPIC_API_KEY` environment variable set in the Omeka container
- PHP GD extension (for image resizing before API calls)

## Database tables

Created automatically on first use:

- **`enrich_field_instructions`** — saved instructions per property (property_id, instructions, model)
- **`enrich_field_cache`** — per-item enrichment cache (item_id, property_id, value, model)
- **`enrich_batch_meta`** — Batch API job metadata (batch_id, property_id, model, status, etc.)
