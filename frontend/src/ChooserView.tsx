import { useAuth } from "./AuthContext";
import { navigateTo } from "./useRoute";
import "./ChooserView.css";

export default function ChooserView() {
  const { logout, user } = useAuth();

  const goCompanion = () => navigateTo("/companion");
  const goDashboard = () => navigateTo("/caregiver");
  const goLogout = () => {
    logout();
    navigateTo("/");
  };

  return (
    <section className="screen on chooser-screen">
      <div className="center">
        <div className="choose">
          <div className="wordmark">Sahaara</div>
          <h2>Welcome back, {user?.username || "Rohan"}</h2>
          <p className="muted">Where would you like to go?</p>
          <div className="opts">
            <button className="opt" onClick={goCompanion}>
              <div
                className="ico"
                style={{
                  background:
                    "radial-gradient(circle at 35% 30%,#fff,#cfc6e6)",
                }}
              />
              <h3>Open Companion</h3>
              <p>
                Asha's screen. It locks, and only you can leave it with your PIN.
              </p>
            </button>
            <button className="opt" onClick={goDashboard}>
              <div
                className="ico"
                style={{
                  background:
                    "linear-gradient(135deg,var(--accent),var(--sage))",
                }}
              />
              <h3>Open Dashboard</h3>
              <p>Today's conversations, alerts and Asha's profile.</p>
            </button>
          </div>
          <button className="btn ghost sm" onClick={goLogout}>
            Sign out
          </button>
        </div>
      </div>
    </section>
  );
}