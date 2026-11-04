import type {TextStyle} from 'react-native';

/**
 * Design tokens for the Razi Automation app.
 *
 * The palette is a deep navy night surface with a blue brand accent taken from
 * the foundation mark, plus warm channel colours for the furnace series.
 */
export const colors = {
  bg: '#050A17',
  bgDeep: '#03060E',
  surface: '#0B1628',
  surfaceAlt: '#101F38',
  surfaceSoft: 'rgba(124, 176, 255, 0.06)',
  border: 'rgba(124, 176, 255, 0.14)',
  borderStrong: 'rgba(124, 176, 255, 0.32)',
  text: '#EDF4FF',
  textSoft: '#A9BDDB',
  muted: '#6C82A6',
  brand: '#2E7BFF',
  brandDeep: '#0B3B8C',
  brandLight: '#7CC0FF',
  cyan: '#37D4FF',
  hot: '#FF7A45',
  warm: '#FFB020',
  mint: '#3EE0A5',
  violet: '#B98CFF',
  danger: '#FF5C7A',
} as const;

export const radius = {
  sm: 12,
  md: 18,
  lg: 24,
  xl: 30,
  pill: 999,
} as const;

export const space = (steps: number) => steps * 4;

/** Fixed-width digits so live values never make the layout jump. */
export const tabular: TextStyle = {fontVariant: ['tabular-nums']};

export const shadow = {
  card: {
    shadowColor: '#000000',
    shadowOpacity: 0.35,
    shadowRadius: 18,
    shadowOffset: {width: 0, height: 10},
    elevation: 8,
  },
  glow: {
    shadowColor: '#2E7BFF',
    shadowOpacity: 0.55,
    shadowRadius: 22,
    shadowOffset: {width: 0, height: 0},
    elevation: 10,
  },
} as const;
