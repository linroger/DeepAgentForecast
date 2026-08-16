import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const here = path.dirname(fileURLToPath(import.meta.url))
const outputDir = process.env.DIAGRAM_OUTPUT_DIR || path.resolve(here, '..')

function architectureExportPlugin() {
  return {
    name: 'architecture-export-endpoint',
    configureServer(server) {
      server.middlewares.use('/__export', async (request, response) => {
        if (request.method !== 'POST') {
          response.statusCode = 405
          response.end('POST required')
          return
        }

        try {
          const chunks = []
          for await (const chunk of request) chunks.push(chunk)
          const payload = JSON.parse(Buffer.concat(chunks).toString('utf8'))
          await fs.mkdir(outputDir, { recursive: true })
          await Promise.all([
            fs.writeFile(
              path.join(outputDir, 'deerflow2-architecture.tldr'),
              Buffer.from(payload.tldrBase64, 'base64'),
            ),
            fs.writeFile(
              path.join(outputDir, 'deerflow2-architecture.svg'),
              payload.svg,
              'utf8',
            ),
            fs.writeFile(
              path.join(outputDir, 'deerflow2-architecture.png'),
              Buffer.from(payload.pngBase64, 'base64'),
            ),
            fs.writeFile(
              path.join(outputDir, 'render-metadata.json'),
              JSON.stringify(payload.metadata, null, 2) + '\n',
              'utf8',
            ),
          ])
          response.setHeader('Content-Type', 'application/json')
          response.end(JSON.stringify({ ok: true, outputDir }))
        } catch (error) {
          response.statusCode = 500
          response.end(String(error?.stack || error))
        }
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), architectureExportPlugin()],
  server: { port: 4178, strictPort: true },
})
