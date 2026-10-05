import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.jsx';
import './styles.css';
import './connected.css';

class ErrorBoundary extends React.Component {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (this.state.failed) return <main className="login-shell"><section className="panel login-card"><h1>Unable to display this view</h1><p>Reload the dashboard. If you just submitted an action, check its saved status before repeating it.</p><button className="primary" onClick={() => location.reload()}>Reload dashboard</button></section></main>;
    return this.props.children;
  }
}
createRoot(document.getElementById('root')).render(<ErrorBoundary><App /></ErrorBoundary>);
