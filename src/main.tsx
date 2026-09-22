import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.tsx';
import './index.css';

function Root() {
  return (
    <>
      <App />

      <div className="fixed bottom-4 right-4 z-[100] flex max-w-[calc(100vw-2rem)] flex-col gap-2 sm:bottom-5 sm:right-5 sm:flex-row">
        <a
          href="/neo-meme-coins-extension.zip"
          download
          className="flex h-11 items-center justify-center rounded-xl bg-emerald-400 px-4 text-xs font-black text-[#06100c] shadow-2xl shadow-black/40 transition hover:bg-emerald-300"
        >
          ИЗТЕГЛИ EXTENSION
        </a>
        <a
          href="/install.html"
          className="flex h-11 items-center justify-center rounded-xl border border-white/15 bg-[#0d0f13]/95 px-4 text-xs font-black text-white shadow-2xl shadow-black/40 backdrop-blur-xl transition hover:border-emerald-400/30 hover:bg-[#141820]"
        >
          КАК СЕ ИНСТАЛИРА
        </a>
      </div>
    </>
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
