// The six consistency pillars are global data, shared with the API.
import data from "../../../data/pillars.json";

export type Pillar = { key: string; label: string };

export const PILLARS: readonly Pillar[] = data.pillars;
