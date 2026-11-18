import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { ConfirmProvider } from "./components/ConfirmDialog";
import { i18nReady } from "./i18n";
import "./styles.css";
import { applySavedTheme } from "./theme";

applySavedTheme();

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false } },
});

// The first render waits for the language's strings, so no screen shows bare keys.
void i18nReady.then(() =>
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <ConfirmProvider>
            <App />
          </ConfirmProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </StrictMode>,
  ),
);
