import { useState } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { makeQueryClient } from './api/queryClient';
import { Layout } from './components/Layout';
import { Tenders } from './pages/Tenders';
import { Profiles } from './pages/Profiles';
import { Opportunities } from './pages/Opportunities';
import './styles.css';

export function App() {
  const [client] = useState(makeQueryClient);
  return (
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Navigate to="/tenders" replace />} />
            <Route path="tenders/*" element={<Tenders />} />
            <Route path="profiles" element={<Profiles />} />
            <Route path="opportunities" element={<Opportunities />} />
            <Route
              path="*"
              element={<p role="alert">Page not found. Use the TenderCite navigation.</p>}
            />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
