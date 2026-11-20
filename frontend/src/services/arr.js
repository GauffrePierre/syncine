import { api } from './api'

// Radarr / Sonarr : dossiers de destination et profils de qualité proposés (administrateur principal)
export const arr = {
  options: (kind) => api.get(`/arr/${kind}/options`),
}
