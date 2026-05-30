import { gql } from "graphql-request";
import { z } from "zod";
const GetPagesInputSchema = z.object({
    query: z.string().optional().describe("Shopify search query, e.g. \"handle:paying-with-zcash\" or a title fragment"),
    limit: z.number().default(20),
});
let shopifyClient;
const getPages = {
    name: "get-pages",
    description: "List Online Store pages (id, title, handle, publish state, body). Use a query like handle:paying-with-zcash to find a specific page's GID before updating it.",
    schema: GetPagesInputSchema,
    initialize(client) {
        shopifyClient = client;
    },
    execute: async (input) => {
        try {
            const { query: searchQuery, limit } = input;
            const query = gql `
        query getPages($first: Int!, $query: String) {
          pages(first: $first, query: $query) {
            nodes {
              id
              title
              handle
              isPublished
              publishedAt
              templateSuffix
              bodySummary
              updatedAt
            }
          }
        }
      `;
            const variables = { first: limit, query: searchQuery };
            const data = (await shopifyClient.request(query, variables));
            return { pages: data.pages.nodes };
        }
        catch (error) {
            console.error("Error fetching pages:", error);
            throw new Error(`Failed to fetch pages: ${error instanceof Error ? error.message : String(error)}`);
        }
    },
};
export { getPages };
