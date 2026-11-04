import React, {useRef} from 'react';
import {Animated, Pressable, StyleSheet, Text, View} from 'react-native';

import type {ChannelReading} from '../data/feed';
import {colors, radius, space, tabular} from '../theme';
import {formatDelta, formatTemp, trendColor, trendLabel, trendOf} from '../utils/format';
import {Sparkline} from './Sparkline';

type Props = {
  reading: ChannelReading;
  active: boolean;
  onPress: () => void;
};

/** Compact furnace tile: live value, short trend and a sparkline. */
export function ChannelCard({reading, active, onPress}: Props) {
  const scale = useRef(new Animated.Value(1)).current;
  const trend = trendOf(reading.delta);
  const accent = trendColor[trend];
  const values = reading.history.slice(-26).map(point => point.v);

  const press = (to: number) => {
    Animated.spring(scale, {
      toValue: to,
      friction: 6,
      tension: 180,
      useNativeDriver: true,
    }).start();
  };

  return (
    <Animated.View style={{flex: 1, transform: [{scale}]}}>
      <Pressable
        onPress={onPress}
        onPressIn={() => press(0.97)}
        onPressOut={() => press(1)}
        style={[
          styles.card,
          active && {borderColor: reading.color, backgroundColor: colors.surfaceAlt},
        ]}>
        <View style={styles.header}>
          <View style={[styles.dot, {backgroundColor: reading.color}]} />
          <Text style={styles.name}>{reading.name}</Text>
        </View>

        <View style={styles.valueRow}>
          <Text style={styles.value}>{formatTemp(reading.temp)}</Text>
          <Text style={styles.unit}>°C</Text>
        </View>

        <View style={styles.footer}>
          <Text style={[styles.delta, {color: accent}]}>
            {formatDelta(reading.delta)}
          </Text>
          <Sparkline values={values} color={reading.color} width={64} height={28} />
        </View>

        <Text style={[styles.state, {color: accent}]}>{trendLabel[trend]}</Text>
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    borderWidth: 1,
    borderColor: colors.border,
    padding: space(4),
    minHeight: 128,
    justifyContent: 'space-between',
  },
  header: {flexDirection: 'row-reverse', alignItems: 'center'},
  dot: {width: 8, height: 8, borderRadius: 4, marginLeft: space(2)},
  name: {
    color: colors.textSoft,
    fontSize: 12.5,
    fontWeight: '600',
    textAlign: 'right',
    flexShrink: 1,
  },
  valueRow: {
    flexDirection: 'row-reverse',
    alignItems: 'flex-end',
    marginTop: space(2),
  },
  value: {...tabular, color: colors.text, fontSize: 26, fontWeight: '700', letterSpacing: -0.6},
  unit: {color: colors.muted, fontSize: 11, marginRight: 3, marginBottom: 5},
  footer: {
    flexDirection: 'row-reverse',
    alignItems: 'flex-end',
    justifyContent: 'space-between',
    marginTop: space(1),
  },
  delta: {...tabular, fontSize: 11.5, fontWeight: '700'},
  state: {...tabular, fontSize: 10.5, marginTop: space(1.5), opacity: 0.9, textAlign: 'right'},
});
