import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader2, Plus, Trash2 } from 'lucide-react'
import Button from '../ui/Button'
import { arr } from '../../services/arr'
import { useUpdateSettings } from '../../hooks/useMedia'
import { toast } from '../../store/uiStore'
import { useT } from '../../i18n'

// 16 px sur téléphone : en dessous, Safari (iPhone) zoome sur le champ touché et la page saute
const input = 'h-9 w-full rounded-lg border border-line bg-bg px-2 text-base text-text outline-none focus:border-accent sm:text-sm'

// Genre pour lequel une catégorie est proposée à l'acceptation (règles du serveur : arr_categories.RULES).
// « default » : tout le reste, nommé Films (Radarr) ou Séries (Sonarr)
export const RULE_LABELS = {
  default: { radarr: 'Films (tout le reste)', sonarr: 'Séries (tout le reste)' },
  documentary: 'Documentaires',
  music: 'Musique, concerts',
  standup: 'Spectacles (stand-up)',
  animation: 'Animation',
  anime: 'Animes (animation japonaise)',
}

// Catégories habituelles, proposées en un clic (dossier et qualité restent à choisir)
const PRESETS = {
  radarr: [
    ['Films', 'default'],
    ['Spectacles', 'standup'],
    ['Reportages', 'documentary'],
    ['Concerts', 'music'],
  ],
  sonarr: [
    ['Séries', 'default'],
    ['Dessins animés', 'animation'],
    ['Animes', 'anime'],
    ['Reportages', 'documentary'],
  ],
}

const newId = () => `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`
const gb = (bytes) => (bytes ? Math.round(bytes / 1e9) : null)

/**
 * Radarr / Sonarr : catégories (films, spectacles, reportages… ; séries, dessins animés, animes…), chacune avec son
 * dossier de destination et sa qualité par défaut. Dossiers et qualités sont lus dans Radarr / Sonarr.
 */
export default function ArrOptions({ kind, settings, configured }) {
  const t = useT()
  const update = useUpdateSettings()
  const savedJson = JSON.stringify(settings?.[`${kind}_categories`] || [])
  const saved = JSON.parse(savedJson)
  const [cats, setCats] = useState(saved)
  // Modifications en cours gardées tant que les catégories enregistrées ne changent pas (autre réglage enregistré,
  // réglages relus…) : seul un nouvel enregistrement de ces catégories les remplace
  useEffect(() => setCats(JSON.parse(savedJson)), [savedJson])
  const { data, isLoading, error } = useQuery({
    queryKey: ['arr-options', kind, settings?.[`${kind}_url`]],
    queryFn: () => arr.options(kind),
    enabled: !!configured,
    retry: false,
  })
  if (!configured) return null
  if (isLoading) return <Loader2 className="h-4 w-4 animate-spin text-text3" />
  if (error) return <p className="text-xs text-red-400">{t(error.message)}</p>

  const name = kind === 'radarr' ? 'Radarr' : 'Sonarr'
  const dirty = JSON.stringify(cats) !== JSON.stringify(saved)
  const set = (id, patch) => setCats((list) => list.map((c) => (c.id === id ? { ...c, ...patch } : c)))
  const add = (catName = '', rule = 'default') => ({ id: newId(), name: catName, rule, root_folder: data.root_folders[0]?.path || '', quality_profile: 0 })
  const addPresets = () =>
    setCats((list) => [...list, ...PRESETS[kind].filter(([, rule]) => !list.some((c) => c.rule === rule)).map(([n, rule]) => add(t(n), rule))])
  const save = async () => {
    const clean = cats.filter((c) => c.name.trim())
    await update.mutateAsync({ [`${kind}_categories`]: clean })
    toast(t('Catégories enregistrées'), 'success')
  }
  const incomplete = !cats.some((c) => c.name.trim() && c.root_folder && c.quality_profile)

  return (
    <div className="mt-3 space-y-3">
      <p className="text-xs text-text2">
        {t('En acceptant une demande, tu choisis sa catégorie (proposée d’après le genre du titre) et sa qualité. {name} cherche et télécharge le titre dans le dossier de la catégorie.', { name })}
      </p>
      {cats.map((c) => (
        <div key={c.id} className="space-y-2 rounded-xl border border-line bg-bg p-3">
          <div className="flex gap-2">
            <input value={c.name} onChange={(e) => set(c.id, { name: e.target.value })} placeholder={t('Nom de la catégorie')} aria-label={t('Nom de la catégorie')} maxLength={60} className={input} />
            <button type="button" onClick={() => setCats((list) => list.filter((x) => x.id !== c.id))} className="shrink-0 rounded-lg px-2 text-text3 hover:bg-bg3 hover:text-red-400" title={t('Supprimer')}>
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
          <div className="grid gap-2 sm:grid-cols-3">
            <label className="block">
              <span className="mb-1 block text-[11px] text-text3">{t('Proposée pour')}</span>
              <select value={c.rule} onChange={(e) => set(c.id, { rule: e.target.value })} className={input}>
                {Object.entries(RULE_LABELS).map(([rule, label]) => (
                  <option key={rule} value={rule}>
                    {t(typeof label === 'string' ? label : label[kind])}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="mb-1 block text-[11px] text-text3">{t('Dossier de destination')}</span>
              <select value={c.root_folder} onChange={(e) => set(c.id, { root_folder: e.target.value })} className={input}>
                <option value="">{t('Choisir…')}</option>
                {data.root_folders.map((f) => (
                  <option key={f.path} value={f.path}>
                    {gb(f.free) != null ? t('{path} ({n} Go libres)', { path: f.path, n: gb(f.free) }) : f.path}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="mb-1 block text-[11px] text-text3">{t('Qualité par défaut')}</span>
              <select value={c.quality_profile} onChange={(e) => set(c.id, { quality_profile: Number(e.target.value) })} className={input}>
                <option value={0}>{t('Choisir…')}</option>
                {data.quality_profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>
      ))}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="ghost" icon={Plus} onClick={() => setCats((list) => [...list, add()])}>
          {t('Ajouter une catégorie')}
        </Button>
        {PRESETS[kind].some(([, rule]) => !cats.some((c) => c.rule === rule)) && (
          <Button size="sm" variant="ghost" onClick={addPresets}>
            {kind === 'radarr' ? t('Ajouter films, spectacles, reportages, concerts') : t('Ajouter séries, dessins animés, animes, reportages')}
          </Button>
        )}
        {dirty && (
          <Button size="sm" variant="primary" loading={update.isPending} onClick={save}>
            {t('Enregistrer')}
          </Button>
        )}
      </div>
      {incomplete && <p className="text-xs text-amber-500">{t('Crée au moins une catégorie avec son dossier et sa qualité : en attendant, les demandes ne partent pas vers {name}.', { name })}</p>}
    </div>
  )
}
