import { api } from './api'

// Demandes d'ajout à la bibliothèque : en attente pour l'administrateur, puis envoyées à Radarr / Sonarr (ou Seerr)
export const requests = {
  list: (scope = 'mine') => api.get(`/requests${scope === 'all' ? '?scope=all' : ''}`),
  // choice : catégorie et qualité Radarr / Sonarr (demande de l'administrateur, envoyée tout de suite)
  create: (item, choice) =>
    api.post('/requests', {
      tmdb_id: Number(item.id),
      media_type: item.media_type,
      title: item.title,
      year: item.year,
      poster: item.poster_small || item.poster,
      ...(choice || {}),
    }),
  choices: (mediaType, tmdbId) => api.get(`/requests/choices/${mediaType}/${tmdbId}`),
  acceptOptions: (id) => api.get(`/requests/${id}/accept`),
  accept: (id, choice) => api.post(`/requests/${id}/send`, choice || undefined),
  setStatus: (id, status) => api.patch(`/requests/${id}`, { status }),
  remove: (id) => api.del(`/requests/${id}`),
}

export const REQUEST_STATUS = {
  pending: { label: 'En attente', cls: 'bg-amber-500/15 text-amber-500' },
  sent: { label: 'Envoyée à Seerr', cls: 'bg-seerr/15 text-seerr' },
  sent_radarr: { label: 'Envoyée à Radarr', cls: 'bg-seerr/15 text-seerr' },
  sent_sonarr: { label: 'Envoyée à Sonarr', cls: 'bg-seerr/15 text-seerr' },
  declined: { label: 'Refusée', cls: 'bg-red-500/15 text-red-400' },
  available: { label: 'Disponible', cls: 'bg-emerald-500/15 text-emerald-500' },
}

// Statut affiché d'une demande (« envoyée » : selon sa destination)
export const requestStatus = (r) =>
  REQUEST_STATUS[r?.status === 'sent' && r.sent_to && r.sent_to !== 'seerr' ? `sent_${r.sent_to}` : r?.status] || REQUEST_STATUS.pending
