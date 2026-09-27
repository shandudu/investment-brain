interface Fetcher {
  fetch(request: Request): Promise<Response>;
}

// Cloudflare provides the concrete runtime binding; keeping this ambient shim
// broad avoids coupling the local app to a deployment-only type package.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type D1Database = any;

declare module "cloudflare:workers" {
  export const env: { DB?: D1Database };
}
