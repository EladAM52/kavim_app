import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import App from './App';
import { initI18n } from './i18n';
import './styles/index.css';

/**
 * i18n is initialized *before* the first render, deliberately.
 *
 * Mounting first would paint the UI in the wrong direction and then flip it —
 * visible, jarring, and worse on a slow phone. Resolving the locale first costs
 * nothing because the bundles are already in the main chunk.
 */
async function bootstrap(): Promise<void> {
  await initI18n();

  const container = document.getElementById('root');
  if (!container) {
    throw new Error('#root not found in index.html');
  }

  createRoot(container).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

void bootstrap();
