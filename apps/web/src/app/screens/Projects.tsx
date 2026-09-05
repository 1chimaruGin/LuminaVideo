/**
 * Your videos.
 *
 * Series memory is the point: episodes belong to a channel and inherit its look, so they are
 * shown as a run of episodes rather than a folder of files.
 */
import { useMemo, useState } from 'react'

import type { ProjectOut } from '@lumina/client'

import { useDeleteProject, useProjects } from '../../api/queries'
import { RECIPES } from '../data'
import { Head, View } from '../Shell'
import { IconPlus, IconTrash } from '../icons'

/**
 * A plate per project.
 *
 * Derived from the id rather than stored: it has to be stable across reloads, and a random
 * colour that changes every render makes the gallery impossible to scan. There is no
 * thumbnail until a render exists, and inventing one would be a lie about what is finished.
 */
function plate(id: string): string {
  const hue = [...id].reduce((n, ch) => (n * 31 + ch.charCodeAt(0)) % 360, 7)
  return `linear-gradient(150deg, hsl(${hue} 42% 26%), hsl(${(hue + 40) % 360} 38% 9%) 72%)`
}

export function Projects({
  onOpen,
  onNew,
}: {
  onOpen: (projectId: string, recipe: string) => void
  onNew: () => void
}) {
  const projects = useProjects()
  const remove = useDeleteProject()
  const rows = projects.data ?? []
  /*
   * Deleting asks first, on the card itself.
   *
   * A confirm step because there is no undo — the project, its plan and its renders go — and
   * inline rather than in a dialog because the thing being confirmed is *which* one, and a
   * modal takes that off screen at the moment it matters most.
   */
  const [confirming, setConfirming] = useState<string | null>(null)

  /**
   * Which task's videos to show.
   *
   * Five lanes make five kinds of thing, and by the time there are more than a screenful the
   * list is not one list — a creator looking for a subtitle job is not scanning past their
   * explainers to find it. Counted from what is actually there, so a lane nobody has used
   * does not offer an empty filter.
   */
  const [lane, setLane] = useState('all')
  const tally = useMemo(() => {
    const counts = new Map<string, number>()
    for (const p of rows) counts.set(p.recipe, (counts.get(p.recipe) ?? 0) + 1)
    return RECIPES.filter((r) => counts.has(r.id)).map((r) => ({
      id: r.id,
      name: r.name,
      n: counts.get(r.id) ?? 0,
    }))
  }, [rows])
  const shown = lane === 'all' ? rows : rows.filter((p) => p.recipe === lane)

  return (
    <View
      action={
        <button className="btn primary block" onClick={onNew}>
          <IconPlus size={19} />
          New video
        </button>
      }
    >
      <Head
        kicker="Your videos"
        title={rows.length ? 'The series so far' : 'Nothing here yet'}
        lede={
          rows.length
            ? 'Every new one inherits the voice, palette and caption style from these.'
            : 'Make your first video and it will show up here.'
        }
      />

      {projects.isLoading ? <p className="muted">Looking…</p> : null}

      {/* Only once there is more than one kind: a filter over a single lane is furniture. */}
      {tally.length > 1 ? (
        <div className="chips" role="group" aria-label="Filter by task">
          <button
            className="chip"
            aria-pressed={lane === 'all'}
            onClick={() => setLane('all')}
          >
            All <small>{rows.length}</small>
          </button>
          {tally.map((t) => (
            <button
              key={t.id}
              className="chip"
              aria-pressed={lane === t.id}
              onClick={() => setLane(t.id)}
            >
              {t.name} <small>{t.n}</small>
            </button>
          ))}
        </div>
      ) : null}

      <div className="gallery">
        {shown.map((p) => (
          <Card
            key={p.id}
            project={p}
            confirming={confirming === p.id}
            busy={remove.isPending && remove.variables === p.id}
            onOpen={() => onOpen(p.id, p.recipe)}
            onAsk={() => setConfirming(confirming === p.id ? null : p.id)}
            onDelete={() => {
              setConfirming(null)
              remove.mutate(p.id)
            }}
          />
        ))}
      </div>
    </View>
  )
}

function Card({
  project,
  confirming,
  busy,
  onOpen,
  onAsk,
  onDelete,
}: {
  project: ProjectOut
  confirming: boolean
  busy: boolean
  onOpen: () => void
  onAsk: () => void
  onDelete: () => void
}) {
  const lane = RECIPES.find((r) => r.id === project.recipe)
  const planned = Boolean(project.current_plan_id)

  return (
    // A div, not a button: it holds buttons, and a button inside a button is invalid markup
    // that browsers resolve by dropping one of them.
    <div
      className="frame card-project"
      data-busy={busy}
      style={{ backgroundImage: plate(project.id), aspectRatio: '4 / 5' }}
    >
      <button className="card-open" onClick={onOpen} aria-label={`Open ${project.title}`} />

      <span className="veil">
        <p className="line">{project.title}</p>
        <span className="tc">
          {lane?.name ?? project.recipe} · {project.language.toUpperCase()}
        </span>
      </span>

      <span style={{ position: 'absolute', top: 10, left: 10 }}>
        <span className={`tag ${planned ? 'good' : ''}`}>{planned ? 'Planned' : 'Draft'}</span>
      </span>

      <button
        className="card-bin"
        onClick={onAsk}
        aria-label={confirming ? `Cancel deleting ${project.title}` : `Delete ${project.title}`}
        title="Delete"
      >
        <IconTrash size={15} />
      </button>

      {confirming ? (
        <div className="card-sure">
          <b>Delete this?</b>
          <small>It and everything made for it go. Credits it is holding come back.</small>
          <span>
            <button onClick={onAsk}>Keep</button>
            <button data-bad onClick={onDelete}>
              Delete
            </button>
          </span>
        </div>
      ) : null}
    </div>
  )
}
