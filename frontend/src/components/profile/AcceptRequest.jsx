import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, Loader2 } from 'lucide-react'
import Modal from '../ui/Modal'
import Button from '../ui/Button'
import { requests } from '../../services/requests'
import { toast } from '../../store/uiStore'
import { useT } from '../../i18n'

// 16 px sur téléphone : en dessous, Safari (iPhone) zoome sur le champ touché
const input = 'h-10 w-full rounded-xl border border-line bg-bg px-3 text-base text-text outline-none focus:border-accent sm:text-sm'
const LABELS = { radarr: 'Radarr', sonarr: 'Sonarr' }

/**
 * Envoi à Radarr / Sonarr : catégorie (proposée d'après le genre du titre) et qualité.
 * - request : accepter la demande d'un membre (onglet Demandes).
 * - item : demande de l'administrateur depuis la fiche d'un titre, envoyée tout de suite.
 */
export default function AcceptRequest({ request, item, onClose }) {
  const t = useT()
  const qc = useQueryClient()
  const title = request?.title || item?.title
  const { data, isLoading, error } = useQuery({
    queryKey: request ? ['request-accept', request.id] : ['request-choices', item.media_type, item.id],
    queryFn: () => (request ? requests.acceptOptions(request.id) : requests.choices(item.media_type, item.id)),
    retry: false,
  })
  const [category, setCategory] = useState('')
  const [quality, setQuality] = useState(0)
  const cats = data?.categories || []
  const pick = (id) => {
    setCategory(id)
    setQuality(cats.find((c) => c.id === id)?.quality_profile || 0)
  }
  useEffect(() => {
    if (data?.suggested) pick(data.suggested)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data])

  const accept = useMutation({
    mutationFn: () => {
      const choice = { category, quality_profile: quality || null }
      return request ? requests.accept(request.id, choice) : requests.create(item, choice)
    },
    onSuccess: (r) => {
      if (request) toast(t('Demande acceptée : envoyée à {name}', { name: LABELS[data.destination] }), 'success')
      else toast(t(r.message || 'Demande déjà en cours'), r.request?.status === 'pending' ? 'error' : 'success')
      qc.invalidateQueries({ queryKey: ['requests'] })
      qc.invalidateQueries({ queryKey: ['media'] })
      onClose()
    },
    onError: (e) => toast(t(e.message), 'error'),
  })

  return (
    <Modal open onClose={onClose} title={request ? t('Accepter « {title} »', { title }) : t('Envoyer « {title} » à {name}', { title, name: LABELS[data?.destination] || 'Radarr' })}>
      {isLoading && <Loader2 className="mx-auto h-5 w-5 animate-spin text-text3" />}
      {error && <p className="text-sm text-red-400">{t(error.message)}</p>}
      {data && (
        <div className="space-y-4">
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-text2">{t('Catégorie')}</span>
            <select value={category} onChange={(e) => pick(e.target.value)} className={input}>
              {cats.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                  {c.id === data.suggested ? ` · ${t('proposée')}` : ''}
                </option>
              ))}
            </select>
          </label>
          {data.quality_profiles?.length > 0 && (
            <label className="block">
              <span className="mb-1.5 block text-xs font-medium text-text2">{t('Qualité')}</span>
              <select value={quality} onChange={(e) => setQuality(Number(e.target.value))} className={input}>
                {data.quality_profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <p className="text-xs text-text3">{t('{name} ajoute le titre dans le dossier de la catégorie et lance sa recherche.', { name: LABELS[data.destination] })}</p>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose}>
              {t('Annuler')}
            </Button>
            <Button variant="primary" icon={Download} loading={accept.isPending} disabled={!category} onClick={() => accept.mutate()}>
              {request ? t('Accepter') : t('Envoyer')}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  )
}
