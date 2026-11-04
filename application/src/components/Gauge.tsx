import React, {useEffect, useRef} from 'react';
import {Animated, Easing, StyleSheet, View} from 'react-native';
import Svg, {Circle, Defs, LinearGradient, Stop} from 'react-native-svg';

import {colors} from '../theme';
import {clamp} from '../utils/format';

const AnimatedCircle = Animated.createAnimatedComponent(Circle);

type Props = {
  value: number | null;
  min: number;
  max: number;
  color: string;
  size?: number;
  thickness?: number;
  children?: React.ReactNode;
};

/**
 * 270° arc gauge. The arc sweeps from the bottom-left, the gradient follows the
 * channel colour and the value animates instead of jumping between samples.
 */
export function Gauge({
  value,
  min,
  max,
  color,
  size = 248,
  thickness = 16,
  children,
}: Props) {
  const fraction = clamp(
    ((value ?? min) - min) / Math.max(1, max - min),
    0,
    1,
  );
  const animated = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.timing(animated, {
      toValue: fraction,
      duration: 950,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: false,
    }).start();
  }, [animated, fraction]);

  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;
  const arcLength = circumference * 0.75;
  const center = size / 2;

  const dashoffset = animated.interpolate({
    inputRange: [0, 1],
    outputRange: [circumference, circumference - arcLength],
  });

  return (
    <View style={{width: size, height: size, alignItems: 'center', justifyContent: 'center'}}>
      <Svg width={size} height={size} style={StyleSheet.absoluteFill}>
        <Defs>
          <LinearGradient id="gauge-arc" x1="0" y1="0" x2="1" y2="1">
            <Stop offset="0" stopColor={color} stopOpacity={0.55} />
            <Stop offset="0.55" stopColor={color} />
            <Stop offset="1" stopColor={colors.brandLight} />
          </LinearGradient>
        </Defs>

        <Circle
          cx={center}
          cy={center}
          r={radius}
          stroke="rgba(124, 176, 255, 0.12)"
          strokeWidth={thickness}
          strokeLinecap="round"
          strokeDasharray={`${arcLength} ${circumference}`}
          fill="none"
          transform={`rotate(135 ${center} ${center})`}
        />

        <AnimatedCircle
          cx={center}
          cy={center}
          r={radius}
          stroke="url(#gauge-arc)"
          strokeWidth={thickness}
          strokeLinecap="round"
          strokeDasharray={`${circumference} ${circumference}`}
          strokeDashoffset={dashoffset as unknown as number}
          fill="none"
          transform={`rotate(135 ${center} ${center})`}
        />
      </Svg>

      <View style={styles.center}>{children}</View>
    </View>
  );
}

const styles = StyleSheet.create({
  center: {alignItems: 'center', justifyContent: 'center'},
});
