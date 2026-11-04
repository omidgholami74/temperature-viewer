import React from 'react';
import Svg, {Circle, Defs, G, LinearGradient, Path, Stop} from 'react-native-svg';

type Props = {
  size?: number;
  /** `brand` uses the blue gradient, `mono` a single colour. */
  variant?: 'brand' | 'mono';
  color?: string;
};

/** The traced artwork lives in a 360 × 620 box and is centred in a square. */
const SCALE = 0.7364;
const OFFSET_X = 119.2;
const OFFSET_Y = 35.1;

/** Peacock silhouette: beak, head, neck, body and the rising tail blade. */
const BODY =
  'M138 242C162 234 184 222 200 206C184 186 188 156 206 138C2' +
  '24 122 250 128 262 148C272 162 278 178 288 194C302 166 302 96 302 8C350 88 358 176 338 244C320 306 292 362 264 416C246 452 2' +
  '32 484 222 512C214 540 208 566 205 592C198 556 204 514 218 474C236 428 256 390 280 348C268 330 252 3' +
  '20 236 316C220 312 208 300 206 284C204 268 208 254 216 244C190 246 162 246 138 242Z';

/** Three long feathers fanning to the lower left. */
const FEATHERS = [
  'M282 288C240 344 176 438 90 578C152 492 216 402 278 330Z',
  'M284 266C216 294 134 344 14 422C106 370 198 318 276 288Z',
  'M274 238C202 244 122 258 32 286C120 286 202 280 270 260Z',
];

/** Crown of three curled plumes on top of the head. */
const CROWN_STEMS = [
  'M208 142C200 122 190 110 180 98',
  'M222 138C220 114 221 96 223 78',
  'M236 142C244 122 256 110 268 100',
];

const CROWN_TIPS: [number, number][] = [
  [178, 95],
  [223, 75],
  [270, 98],
];

/**
 * Foundation mark, drawn as vector paths so it stays crisp from the 26 px
 * header badge up to the splash screen.
 */
export function BrandLogo({size = 48, variant = 'brand', color = '#FFFFFF'}: Props) {
  const paint = variant === 'mono' ? color : 'url(#razi-mark)';

  return (
    <Svg width={size} height={size} viewBox="0 0 512 512">
      <Defs>
        <LinearGradient
          id="razi-mark"
          x1="150"
          y1="80"
          x2="420"
          y2="480"
          gradientUnits="userSpaceOnUse">
          <Stop offset="0" stopColor="#8FD0FF" />
          <Stop offset="0.45" stopColor="#3E8CFF" />
          <Stop offset="1" stopColor="#1B57BE" />
        </LinearGradient>
      </Defs>

      <G transform={`translate(${OFFSET_X} ${OFFSET_Y}) scale(${SCALE})`}>
        {FEATHERS.map(d => (
          <Path key={d} d={d} fill={paint} />
        ))}
        <Path d={BODY} fill={paint} />

        <G stroke={paint} strokeWidth={6} strokeLinecap="round" fill="none">
          {CROWN_STEMS.map(d => (
            <Path key={d} d={d} />
          ))}
        </G>

        {CROWN_TIPS.map(([cx, cy]) => (
          <Circle key={`${cx}-${cy}`} cx={cx} cy={cy} r={5} fill={paint} />
        ))}
      </G>
    </Svg>
  );
}
