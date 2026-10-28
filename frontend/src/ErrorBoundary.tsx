import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * Keeps one broken panel from blanking the whole screen. Extracted from App so
 * the companion view can use it without importing the caregiver shell (which
 * would pull in auth and alert modules).
 */
export default class ErrorBoundary extends Component<
  { children: ReactNode; name: string; fallback?: ReactNode },
  { hasError: boolean }
> {
  constructor(props: { children: ReactNode; name: string; fallback?: ReactNode }) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[ErrorBoundary:${this.props.name}]`, error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }
      return (
        <div className="error-boundary-fallback">
          <p>Something went wrong in {this.props.name}.</p>
          <button onClick={() => this.setState({ hasError: false })}>Retry</button>
        </div>
      );
    }
    return this.props.children;
  }
}
