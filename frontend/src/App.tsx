import { AuthProvider, useAuth } from "./AuthContext";
import { VoiceStateProvider } from "./VoiceStateContext";
import DashboardView from "./DashboardView";
import CompanionView from "./CompanionView";
import ChooserView from "./ChooserView";
import LoginView from "./LoginView";
import ErrorBoundary from "./ErrorBoundary";
import TaskAlarm from "./TaskAlarm";
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

/* Auth crash fallback */
function AuthCrashFallback() {
  return (
    <div className="error-boundary-fallback">
      <p>Authentication error. Please refresh the page.</p>
      <button onClick={() => window.location.reload()}>Retry</button>
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
      <VoiceStateProvider>
        <ErrorBoundary name="AuthRouter" fallback={<AuthCrashFallback />}>
          <AuthRouter />
        </ErrorBoundary>
      </VoiceStateProvider>
    </AuthProvider>
  );
}

function AuthRouter() {
  const { user, isLoading, authEnabled, authError, retryAuth } = useAuth();

  if (isLoading) {
    return (
      <div className="app-container loading">
        <div className="loading-spinner" />
        <p>Loading...</p>
      </div>
    );
  }

  // The auth check could not reach the server. Fail closed: show an honest
  // retry screen instead of guessing "auth is off" and rendering the
  // caregiver dashboard with nothing behind it.
  if (authError) {
    return (
      <div className="app-container loading">
        <p>We couldn't reach Sahaara. Please check that the server is running.</p>
        <button className="btn" type="button" onClick={() => void retryAuth()}>
          Try again
        </button>
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
  return (
    <>
      <DashboardView />
      <TaskAlarm />
    </>
  );
}

export default App;