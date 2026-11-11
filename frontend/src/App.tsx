import { Providers } from "@/app/Providers";
import { AppRoutes } from "@/app/router";

/**
 * App root. All providers (QueryClient, CommandMenu, Shortcuts,
 * BrowserRouter, Toaster, theme-sync) live in Providers; routes live
 * in AppRoutes. Keep this file small so the entry point stays obvious.
 */
export default function App() {
  return (
    <Providers>
      <AppRoutes />
    </Providers>
  );
}
