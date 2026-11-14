import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import "@designcodeio/threeui/style.css";
import "./styles.css";

import { App } from "./App";
import { ToastProvider } from "./components/Toast";
import { PrefsProvider } from "./state/prefs";

const container = document.getElementById("root");
if (!container) throw new Error("Root element missing");

createRoot(container).render(
  <StrictMode>
    <PrefsProvider>
      <ToastProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </ToastProvider>
    </PrefsProvider>
  </StrictMode>,
);
