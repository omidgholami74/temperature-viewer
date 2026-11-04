import React from 'react';
import Svg, {Circle, Defs, LinearGradient, Path, Stop} from 'react-native-svg';

type Props = {
  size?: number;
  /** `brand` uses the blue gradient, `mono` a single colour. */
  variant?: 'brand' | 'mono';
  color?: string;
};

/**
 * Foundation mark: a stylised peacock, drawn as flowing feathers around a
 * curved body with a crowned head. Vector only, so it stays crisp from the
 * 34 px header badge up to the splash screen.
 */
export function BrandLogo({size = 48, variant = 'brand', color = '#FFFFFF'}: Props) {
  const stroke = variant === 'mono' ? color : 'url(#razi-mark)';
  const dot = variant === 'mono' ? color : 'url(#razi-mark)';

  return (
    <Svg width={size} height={size} viewBox="0 0 512 512">
      <Defs>
        <LinearGradient id="razi-mark" x1="90" y1="60" x2="430" y2="470" gradientUnits="userSpaceOnUse">
          <Stop offset="0" stopColor="#7CC0FF" />
          <Stop offset="0.45" stopColor="#2E7BFF" />
          <Stop offset="1" stopColor="#0B3B8C" />
        </LinearGradient>
      </Defs>

      {/* Feather ribbons fanning to the lower left */}
      <Path
        d="M252 252C186 250 116 268 44 306C116 300 190 288 252 274Z"
        fill={stroke}
        opacity={0.94}
      />
      <Path
        d="M250 286C184 296 118 330 52 386C122 362 192 336 258 312Z"
        fill={stroke}
        opacity={0.98}
      />
      <Path
        d="M260 322C206 346 154 392 104 452C162 416 220 378 274 344Z"
        fill={stroke}
        opacity={0.92}
      />
      <Path
        d="M288 352C258 392 226 436 196 490C238 450 272 410 302 374Z"
        fill={stroke}
        opacity={0.86}
      />

      {/* Rising tail, kept clear of the head */}
      <Path
        d="M342 120C402 138 440 200 446 268C452 348 414 424 340 496C372 428 384 366 378 306C372 232 352 178 330 140Z"
        fill={stroke}
      />

      {/* Neck joining the head to the rising tail */}
      <Path
        d="M322 182C354 198 374 230 386 268C392 288 394 312 392 336C378 296 356 254 316 226Z"
        fill={stroke}
      />

      {/* Head with beak */}
      <Path
        d="M262 122C306 120 330 152 326 186C322 218 296 238 266 240C250 241 238 236 230 227L190 236L214 206C210 176 224 140 262 122Z"
        fill={stroke}
      />

      {/* Crown of knowledge */}
      <Path
        d="M258 118C252 96 246 80 238 64"
        stroke={stroke}
        strokeWidth={9}
        strokeLinecap="round"
        fill="none"
      />
      <Path
        d="M271 116C277 92 285 74 295 60"
        stroke={stroke}
        strokeWidth={9}
        strokeLinecap="round"
        fill="none"
      />
      <Path
        d="M283 124C295 106 309 94 325 86"
        stroke={stroke}
        strokeWidth={9}
        strokeLinecap="round"
        fill="none"
      />
      <Circle cx={236} cy={56} r={11} fill={dot} />
      <Circle cx={296} cy={50} r={10} fill={dot} />
      <Circle cx={328} cy={82} r={9} fill={dot} />
    </Svg>
  );
}
