import { AuthProvider } from "./AuthContext";
import { VoiceStateProvider } from "./VoiceStateContext";
import DashboardView from "./DashboardView";
import CompanionView from "./CompanionView";
import ChooserView from "./ChooserView";
import ErrorBoundary from "./ErrorBoundary";
import { useRoute } from "./useRoute";
import "./App.css";

/* Companion crash fallback */
function CompanionCrashFallback() {
  return (
    <div className="companion-shell companion-shell--error">
      <p>Sahaara is resting for a moment.</p>
      <button type="button" onClick={() => window.location.reload()}>
        Try again
      </button>
    </div>
  );
}

/**
 * Root app with route split.
 *
 * Routes:
 * - /companion          -> CompanionView (no auth, full screen)
 * - /choose             -> ChooserView (after login, pick destination)
 * - /caregiver          -> DashboardView (dashboard)
 * - /                   -> LoginPage (if auth enabled) or redirect to /caregiver
 * - /?demo=split        -> DashboardView with demoSplit=true (hidden demo)
 */
function App() {
  const route = useRoute();

  // Show companion directly (no auth)
  if (route === "companion") {
    return (
      <ErrorBoundary name="CompanionView" fallback={<CompanionCrashFallback />}>
        <VoiceStateProvider>
          <CompanionView />
        </VoiceStateProvider>
      </ErrorBoundary>
    );
  }

  // Show chooser (requires auth)
  if (route === "choose") {
    return (
      <AuthProvider>
        <ChooserView />
      </AuthProvider>
    );
  }

  // Caregiver dashboard (requires auth)
  return (
    <AuthProvider>
      <DashboardView />
    </AuthProvider>
  );
}

export default App;