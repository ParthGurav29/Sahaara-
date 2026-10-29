import { AuthProvider, useAuth } from "./AuthContext";
import { VoiceStateProvider } from "./VoiceStateContext";
import DashboardView from "./DashboardView";
import CompanionView from "./CompanionView";
import ChooserView from "./ChooserView";
import LoginView from "./LoginView";
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

  // Login page or dashboard (requires auth)
  return (
    <AuthProvider>
      <AuthRouter />
    </AuthProvider>
  );
}

function AuthRouter() {
  const { user, isLoading, authEnabled } = useAuth();

  if (isLoading) {
    return (
      <div className="app-container loading">
        <div className="loading-spinner" />
        <p>Loading...</p>
      </div>
    );
  }

  // If auth is disabled, go straight to dashboard
  if (!authEnabled) {
    return <DashboardView />;
  }

  // If no user logged in, show login page
  if (!user) {
    return <LoginView />;
  }

  // User is logged in - show dashboard
  return <DashboardView />;
}

export default App;