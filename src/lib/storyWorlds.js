// Story mode's worlds, in picker order. Ids match STORY_WORLDS in
// backend/app/schemas.py. Each world's title and blurb are i18n keys
// (story.world.<id>.title / .blurb); each scene has story.scene.<id>.title and
// .setup. `cast` is a scene's default lore-book cast. `withCappuccina` is the
// default for "Cappuccina joins the story": on only in her own world.
// Fandom and custom worlds have no presets; the user describes them.
export const STORY_WORLDS = [
  {
    id: 'cappuccina',
    emoji: '☕',
    withCappuccina: true,
    scenes: [
      { id: 'backstage', cast: ['ballerino-lololo', 'cappuccina-canterina'] },
      { id: 'cafe', cast: ['espressona-signora', 'cappuccino-assassino'] },
      { id: 'race', cast: ['tralalero-tralala', 'chimpanzini-bananini'] },
      { id: 'sahur', cast: ['tung-tung-tung-sahur', 'ta-ta-ta-sahur'] },
      { id: 'flight', cast: ['bombardiro-crocodilo'] },
    ],
  },
  { id: 'fantasy', emoji: '🐉', scenes: [{ id: 'tavern' }, { id: 'dragon' }, { id: 'academy' }] },
  { id: 'scifi', emoji: '🚀', scenes: [{ id: 'derelict' }, { id: 'colony' }, { id: 'android' }] },
  { id: 'mystery', emoji: '🔍', scenes: [{ id: 'manor' }, { id: 'train' }, { id: 'school' }] },
  { id: 'historical', emoji: '🏛️', scenes: [{ id: 'rome' }, { id: 'silkroad' }, { id: 'voyage' }] },
  { id: 'everyday', emoji: '🏙️', scenes: [{ id: 'firstday' }, { id: 'roadtrip' }, { id: 'band' }] },
  { id: 'fandom', emoji: '⭐', needsText: true, scenes: [] },
  { id: 'custom', emoji: '✏️', needsText: true, scenes: [] },
]

export function storyWorld(id) {
  return STORY_WORLDS.find((w) => w.id === id) ?? STORY_WORLDS[0]
}
