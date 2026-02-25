// main.tsx
import { createRoot } from "react-dom/client";
import { RouterProvider, createBrowserRouter } from "react-router-dom";
import routes from "./routes";
import "./index.css";

// Register service worker for PWA support
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    let refreshing = false;

    navigator.serviceWorker
      .register("/service-worker.js")
      .then((registration) => {
        const requestSkipWaiting = (worker: ServiceWorker | null) => {
          worker?.postMessage({ type: "SKIP_WAITING" });
        };

        if (registration.waiting) {
          requestSkipWaiting(registration.waiting);
        }

        registration.addEventListener("updatefound", () => {
          const newWorker = registration.installing;
          if (newWorker) {
            newWorker.addEventListener("statechange", () => {
              if (newWorker.state === "installed") {
                requestSkipWaiting(newWorker);
              }
            });
          }
        });
      })
      .catch((err) => {
        console.error("Service Worker registration failed:", err);
      });

    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (refreshing) return;
      refreshing = true;
      window.location.reload();
    });
  });
}

const router = createBrowserRouter(routes, {
  future: {
    v7_startTransition: true,
    v7_relativeSplatPath: true,
  },
});

createRoot(document.getElementById("root")!).render(<RouterProvider router={router} />);
