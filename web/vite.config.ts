import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv, type ConfigEnv, type ProxyOptions } from 'vite';

export function workspaceConfig({ mode, command }: ConfigEnv) {
  const env = loadEnv(mode, process.cwd(), '');
  const tendercite = env.TENDERCITE_PROXY_TARGET;
  if (tendercite) {
    const url = new URL(tendercite);
    if (
      !['http:', 'https:'].includes(url.protocol) ||
      url.username ||
      url.password ||
      url.pathname !== '/' ||
      url.search ||
      url.hash
    )
      throw new Error(
        'TENDERCITE_PROXY_TARGET must be an HTTP(S) origin without credentials or a path.',
      );
  }
  const configured = command === 'serve' && mode === 'development' && Boolean(tendercite);
  const proxy: Record<string, ProxyOptions> = {};
  if (configured && tendercite) {
    proxy['/tendercite-api/api/v1'] = {
      target: tendercite,
      rewrite: (path: string) => path.replace(/^\/tendercite-api/, ''),
      // Keep the existing bounded upload/parsing inactivity timeouts.
      timeout: 180_000,
      proxyTimeout: 180_000,
    };
  }
  return {
    define: { __TENDERCITE_CONFIGURED__: JSON.stringify(configured) },
    plugins: [react()],
    server: {
      host: '127.0.0.1',
      port: 5174,
      strictPort: true,
      proxy,
    },
  };
}

export default defineConfig(workspaceConfig);
