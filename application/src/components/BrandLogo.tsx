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

      {/* Feather ribbons */}
      <Path
        d="M268 212C196 208 118 228 44 288C118 282 196 268 268 246Z"
        fill={stroke}
        opacity={0.92}
      />
      <Path
        d="M262 256C190 264 116 300 48 366C120 342 194 314 266 288Z"
        fill={stroke}
        opacity={0.96}
      />
      <Path
        d="M270 298C212 320 154 368 96 442C160 402 220 360 282 330Z"
        fill={stroke}
        opacity={0.9}
      />
      <Path
        d="M300 342C268 384 232 432 198 492C244 448 282 402 314 366Z"
        fill={stroke}
        opacity={0.84}
      />

      {/* Body and rising tail */}
      <Path
        d="M276 96C372 104 444 184 448 286C452 372 408 448 332 496C372 430 392 368 386 306C380 224 336 152 276 112Z"
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
