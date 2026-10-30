import { useEffect, useState } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import {
  createBrowserRouter,
  createRoutesFromElements,
  Navigate,
  Route,
  RouterProvider,
} from 'react-router-dom';
import { makeQueryClient } from './api/queryClient';
import { Layout } from './components/Layout';
import { Tenders } from './pages/Tenders';
import { Profiles } from './pages/Profiles';
import { Opportunities } from './pages/Opportunities';
import { OpportunityMatching } from './pages/OpportunityMatching';
import { Pipeline } from './pages/Pipeline';
import { Dashboard } from './pages/Dashboard';
import './styles.css';

// Construct once outside React StrictMode in production; tests may own a disposable router.
export function createWorkspaceRouter() {
  return createBrowserRouter(
    createRoutesFromElements(
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="tenders/*" element={<Tenders />} />
        <Route path="profiles" element={<Profiles />} />
        <Route path="opportunities" element={<Opportunities />} />
        <Route path="matching" element={<OpportunityMatching />} />
        <Route path="pipeline" element={<Pipeline />} />
        <Route
          path="*"
          element={<p role="alert">Page not found. Use the TenderCite navigation.</p>}
        />
      </Route>,
    ),
  );
}

export function App({
  router: suppliedRouter,
}: { router?: ReturnType<typeof createWorkspaceRouter> } = {}) {
  const [client] = useState(makeQueryClient);
  const [router] = useState(() => suppliedRouter ?? createWorkspaceRouter());
  useEffect(
    () => () => {
      if (!suppliedRouter) router.dispose();
    },
    [router, suppliedRouter],
  );
  return (
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
