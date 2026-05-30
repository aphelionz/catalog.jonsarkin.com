import { gql } from "graphql-request";
import { z } from "zod";
const UpdatePageInputSchema = z.object({
    id: z.string().min(1).describe("Shopify page GID, e.g. gid://shopify/Page/123. Find it with get-pages."),
    title: z.string().optional(),
    handle: z.string().optional().describe("URL slug for the page"),
    body: z.string().optional().describe("Page content as HTML"),
    isPublished: z.boolean().optional().describe("true = visible, false = hidden"),
    templateSuffix: z.string().optional().describe("Custom page template suffix, e.g. 'contact'"),
    redirectNewHandle: z.boolean().optional().describe("If true, the old handle redirects to the new one"),
});
let shopifyClient;
const updatePage = {
    name: "update-page",
    description: "Update an existing Online Store page (title, handle, body HTML, publish state, template).",
    schema: UpdatePageInputSchema,
    initialize(client) {
        shopifyClient = client;
    },
    execute: async (input) => {
        try {
            const { id, ...pageFields } = input;
            const query = gql `
        mutation pageUpdate($id: ID!, $page: PageUpdateInput!) {
          pageUpdate(id: $id, page: $page) {
            page {
              id
              title
              handle
              isPublished
              publishedAt
              templateSuffix
              updatedAt
            }
            userErrors {
              field
              message
              code
            }
          }
        }
      `;
            const variables = { id, page: pageFields };
            const data = (await shopifyClient.request(query, variables));
            if (data.pageUpdate.userErrors.length > 0) {
                throw new Error(`Failed to update page: ${data.pageUpdate.userErrors
                    .map((e) => `${e.field}: ${e.message}`)
                    .join(", ")}`);
            }
            return { page: data.pageUpdate.page };
        }
        catch (error) {
            console.error("Error updating page:", error);
            throw new Error(`Failed to update page: ${error instanceof Error ? error.message : String(error)}`);
        }
    },
};
export { updatePage };
