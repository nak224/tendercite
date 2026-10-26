import { afterEach, expect, test, vi } from 'vitest';
import { workspaceConfig } from '../../vite.config';

afterEach(() => vi.unstubAllEnvs());
const development = { mode: 'development', command: 'serve' as const };
test('standalone proxy has one fixed prefix and no Team Capacity API target', () => {
  vi.stubEnv('API_PROXY_TARGET', 'http://127.0.0.1:8000');
  vi.stubEnv('TENDERCITE_PROXY_TARGET', '');
  const disabled = workspaceConfig(development);
  expect(disabled.define.__TENDERCITE_CONFIGURED__).toBe('false');
  expect(disabled.server.proxy).toEqual({});
  expect(disabled.server.host).toBe('127.0.0.1');
  expect(disabled.server.port).toBe(5174);
  expect(disabled.server.strictPort).toBe(true);
  vi.stubEnv('TENDERCITE_PROXY_TARGET', 'http://127.0.0.1:8001');
  const enabled = workspaceConfig(development);
  expect(enabled.define.__TENDERCITE_CONFIGURED__).toBe('true');
  expect('/api' in enabled.server.proxy).toBe(false);
  expect('/health' in enabled.server.proxy).toBe(false);
  expect(Object.keys(enabled.server.proxy)).toEqual(['/tendercite-api/api/v1']);
  const proxy = enabled.server.proxy['/tendercite-api/api/v1'];
  expect(proxy?.target).toBe('http://127.0.0.1:8001');
  expect(proxy?.timeout).toBe(180_000);
  expect(proxy?.proxyTimeout).toBe(180_000);
  expect(proxy?.rewrite?.('/tendercite-api/api/v1/documents')).toBe('/api/v1/documents');
  expect(
    workspaceConfig({ mode: 'production', command: 'build' }).define.__TENDERCITE_CONFIGURED__,
  ).toBe('false');
  expect(workspaceConfig({ mode: 'production', command: 'build' }).server.proxy).toEqual({});
});
test.each([
  'file:///etc/passwd',
  'http://user:password@localhost:8001',
  'http://localhost:8001/arbitrary',
  'http://localhost:8001?target=other',
])('rejects non-origin target %s', (target) => {
  vi.stubEnv('TENDERCITE_PROXY_TARGET', target);
  expect(() => workspaceConfig(development)).toThrow();
});
