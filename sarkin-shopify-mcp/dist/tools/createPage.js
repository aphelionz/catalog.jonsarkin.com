import { gql } from "graphql-request";
import { z } from "zod";
const CreatePageInputSchema = z.object({
    title: z.string().min(1).describe("Page title (required)"),
    body: z.string().optional().describe("Page content as HTML"),
    handle: z.string().optional().describe("URL slug; defaults to a slugified title"),
    isPublished: z.boolean().optional().describe("true = visible immediately, false = hidden"),
    templateSuffix: z.string().optional().describe("Custom page template suffix, e.g. 'contact'"),
});
let shopifyClient;
const createPage = {
    name: "create-page",
    description: "Create a new Online Store page.",
    schema: CreatePageInputSchema,
    initialize(client) {
        shopifyClient = client;
    },
    execute: async (input) => {
        try {
            const query = gql `
        mutation pageCreate($page: PageCreateInput!) {
          pageCreate(page: $page) {
            page {
              id
              title
              handle
              isPublished
              publishedAt
              templateSuffix
            }
            userErrors {
              field
              message
              code
            }
          }
        }
      `;
            const variables = { page: input };
            const data = (await shopifyClient.request(query, variables));
            if (data.pageCreate.userErrors.length > 0) {
                throw new Error(`Failed to create page: ${data.pageCreate.userErrors
                    .map((e) => `${e.field}: ${e.message}`)
                    .join(", ")}`);
            }
            return { page: data.pageCreate.page };
        }
        catch (error) {
            console.error("Error creating page:", error);
            throw new Error(`Failed to create page: ${error instanceof Error ? error.message : String(error)}`);
        }
    },
};
export { createPage };
