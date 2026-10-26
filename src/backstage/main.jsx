import React from 'react'
import ReactDOM from 'react-dom/client'
import { LanguageProvider } from '../i18n/LanguageProvider'
import { Backstage } from './Backstage'
import './backstage.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <LanguageProvider titleKey="backstage.pageTitle">
      <Backstage />
    </LanguageProvider>
  </React.StrictMode>,
)
