import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App, createWorkspaceRouter } from './App';

const router = createWorkspaceRouter();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App router={router} />
  </StrictMode>,
);
