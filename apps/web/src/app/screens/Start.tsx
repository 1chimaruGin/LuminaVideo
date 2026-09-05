/**
 * Start — step 1.
 *
 * Five tasks. Pick one. That is the entire screen.
 *
 * It carried a text box as well, and that was the confusion: a screen that asks a question
 * *and* offers five answers has two entry points and no stated relationship between them. The
 * box could not be written properly either, because it did not yet know which task it was
 * collecting for — with Subtitle chosen it still said "Paste a link, write an idea".
 *
 * So the input moved to the next screen, where the task is known and the question can be the
 * right one. Nothing is asked here that cannot be answered by pointing at a picture, which is
 * also what makes this screen work in a language it was not written in.
 */
import { RECIPES } from '../data'
import type { Studio } from '../live'
import { RECIPE_ART } from '../RecipeArt'
import { Head, View } from '../Shell'

export function Start({ s, onNext }: { s: Studio; onNext: () => void }) {
  return (
    // Not `mid`. That caps the column at 640px, which is right for a form and wrong here —
    // five cards folded into a third of the screen, two per row, with the fifth orphaned.
    // These cards *are* the screen.
    <View center>
      <div className="lanes-head">
        <Head
          kicker="Start"
          title="What are we making?"
          lede="Pick one. I'll ask for what it needs next."
        />
      </div>

      <div className="lanes" role="list">
        {RECIPES.map((r) => {
          const Art = RECIPE_ART[r.id] ?? RECIPE_ART.explainer!
          return (
            <button
              key={r.id}
              role="listitem"
              className="lane"
              // Choosing *is* continuing. A separate confirm button would make the creator
              // press twice to say one thing.
              onClick={() => {
                // A new short, not the last one. The open project is remembered across
                // reloads so a closed tab does not lose work — which meant picking a task
                // here landed on step 2 with the *previous* plan already filled in, and no
                // input box, because the screen could see scenes and assumed they were yours.
                s.setProjectId(null)
                s.setRecipe(r.id)
                onNext()
              }}
            >
              <span className="lane-art">
                <Art />
              </span>
              <b>{r.name}</b>
              <small>{r.blurb}</small>
            </button>
          )
        })}
      </div>
    </View>
  )
}
