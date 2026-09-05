/**
 * Entry point for the design reference at /mock.html.
 *
 * Deliberately imports no app styling — not theme.css, not Tailwind. The prototype ships its
 * own reset, and layering the app's preflight on top changes element metrics enough to make
 * the comparison against Lumina-Web.html meaningless.
 */
import React from 'react'
import ReactDOM from 'react-dom/client'

import { PrototypeScreen } from './Prototype'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <PrototypeScreen />
  </React.StrictMode>,
)
