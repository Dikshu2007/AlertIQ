import React from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuth } from './auth'
import Layout from './components/Layout'
import LoginPage from './pages/Login'
import OverviewPage from './pages/Overview'
import AlertsPage from './pages/Alerts'
import IncidentsPage from './pages/Incidents'
import ServicesPage from './pages/Services'
import AssistantPage from './pages/Assistant'
import AnomaliesPage from './pages/Anomalies'
import IntegrationsPage from './pages/Integrations'

export default function App() {
  const { token } = useAuth()
  if (!token) return <LoginPage />
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<OverviewPage />} />
        <Route path="/alerts" element={<AlertsPage />} />
        <Route path="/incidents" element={<IncidentsPage />} />
        <Route path="/services" element={<ServicesPage />} />
        <Route path="/assistant" element={<AssistantPage />} />
        <Route path="/anomalies" element={<AnomaliesPage />} />
        <Route path="/integrations" element={<IntegrationsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  )
}
