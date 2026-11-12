import { defineConfig } from '@hey-api/openapi-ts';

const openapiUrl = process.env.OPENAPI_URL;
if (!openapiUrl) {
  throw new Error('OPENAPI_URL must be provided by Docker Compose');
}

export default defineConfig({
  input: openapiUrl,
  output: 'src/shared/api/generated',
  plugins: ['@hey-api/typescript', '@hey-api/client-fetch', '@hey-api/sdk'],
});
