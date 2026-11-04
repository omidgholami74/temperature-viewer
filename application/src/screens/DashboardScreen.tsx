import React, {useEffect, useMemo, useRef, useState} from 'react';
import {
  Animated,
  Easing,
  Pressable,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  useWindowDimensions,
  View,
} from 'react-native';
import Svg, {
  Defs,
  LinearGradient,
  RadialGradient,
  Rect,
  Stop,
} from 'react-native-svg';

import {BrandLogo} from '../components/BrandLogo';
import {ChannelCard} from '../components/ChannelCard';
import {Gauge} from '../components/Gauge';
import {TemperatureChart} from '../components/TemperatureChart';
import {channelById} from '../data/channels';
import type {FeedSource} from '../data/feed';
import {useTemperatureFeed} from '../data/feed';
import {colors, radius, shadow, space, tabular} from '../theme';
import {
  formatClock,
  formatDelta,
  formatTemp,
  trendColor,
  trendLabel,
  trendOf,
} from '../utils/format';

const SCREEN_PADDING = 16;
const CARD_GAP = 12;

const WINDOWS = [
  {sec: 60, label: '۱ دقیقه'},
  {sec: 300, label: '۵ دقیقه'},
  {sec: 900, label: '۱۵ دقیقه'},
];

type RevealProps = {
  children: React.ReactNode;
  delay?: number;
  style?: object;
};

function Reveal({children, delay = 0, style}: RevealProps) {
  const value = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.timing(value, {
      toValue: 1,
      duration: 620,
      delay,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: true,
    }).start();
  }, [delay, value]);

  return (
    <Animated.View
      style={[
        style,
        {
          opacity: value,
          transform: [
            {
              translateY: value.interpolate({
                inputRange: [0, 1],
                outputRange: [18, 0],
              }),
            },
          ],
        },
      ]}>
      {children}
    </Animated.View>
  );
}

const SOURCE_COPY: Record<FeedSource, {label: string; color: string}> = {
  live: {label: 'زنده', color: colors.mint},
  offline: {label: 'قطع', color: colors.warm},
  connecting: {label: 'اتصال…', color: colors.textSoft},
};

function SourcePill({source}: {source: FeedSource}) {
  const pulse = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, {
          toValue: 1,
          duration: 900,
          easing: Easing.inOut(Easing.quad),
          useNativeDriver: true,
        }),
        Animated.timing(pulse, {
          toValue: 0,
          duration: 900,
          easing: Easing.inOut(Easing.quad),
          useNativeDriver: true,
        }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [pulse]);

  const meta = SOURCE_COPY[source];

  return (
    <View style={[styles.pill, {borderColor: `${meta.color}55`}]}>
      <Animated.View
        style={[
          styles.pillDot,
          {
            backgroundColor: meta.color,
            opacity: pulse.interpolate({inputRange: [0, 1], outputRange: [0.35, 1]}),
          },
        ]}
      />
      <Text style={[styles.pillText, {color: meta.color}]}>{meta.label}</Text>
    </View>
  );
}

function AppBackground() {
  return (
    <View pointerEvents="none" style={StyleSheet.absoluteFill}>
      <Svg style={StyleSheet.absoluteFill} width="100%" height="100%">
        <Defs>
          <RadialGradient id="bg-top" cx="18%" cy="6%" r="62%">
            <Stop offset="0" stopColor="#1C4C9E" stopOpacity={0.55} />
            <Stop offset="1" stopColor="#050A17" stopOpacity={0} />
          </RadialGradient>
          <RadialGradient id="bg-bottom" cx="92%" cy="92%" r="60%">
            <Stop offset="0" stopColor="#0E3B7A" stopOpacity={0.45} />
            <Stop offset="1" stopColor="#050A17" stopOpacity={0} />
          </RadialGradient>
        </Defs>
        <Rect width="100%" height="100%" fill={colors.bg} />
        <Rect width="100%" height="100%" fill="url(#bg-top)" />
        <Rect width="100%" height="100%" fill="url(#bg-bottom)" />
      </Svg>
    </View>
  );
}

export function DashboardScreen() {
  const feed = useTemperatureFeed();
  const {width} = useWindowDimensions();
  const [selectedId, setSelectedId] = useState(1);
  const [windowSec, setWindowSec] = useState(300);
  const [hidden, setHidden] = useState<number[]>([]);

  const selected = feed.channels.find(channel => channel.id === selectedId) ?? feed.channels[0];
  const meta = channelById(selected.id);
  const trend = trendOf(selected.delta);
  const accent = trendColor[trend];

  const stats = useMemo(() => {
    const from = feed.lastUpdate - windowSec * 1000;
    const values = selected.history
      .filter(point => point.t >= from)
      .map(point => point.v);

    if (values.length === 0) {
      return {min: null as number | null, max: null as number | null, avg: null as number | null};
    }

    const min = Math.min(...values);
    const max = Math.max(...values);
    const avg = values.reduce((sum, value) => sum + value, 0) / values.length;

    return {min, max, avg};
  }, [feed.lastUpdate, selected.history, windowSec]);

  const series = feed.channels
    .filter(channel => !hidden.includes(channel.id))
    .map(channel => ({
      id: channel.id,
      name: channel.name,
      color: channel.color,
      points: channel.history,
    }));

  const cardWidth = (width - SCREEN_PADDING * 2 - CARD_GAP) / 2;
  const chartWidth = width - SCREEN_PADDING * 2 - space(8);

  const toggleSeries = (id: number) => {
    setHidden(current =>
      current.includes(id) ? current.filter(item => item !== id) : [...current, id],
    );
  };

  return (
    <View style={styles.root}>
      <AppBackground />

      <ScrollView
        contentContainerStyle={[
          styles.scroll,
          {paddingTop: (StatusBar.currentHeight ?? 24) + space(2)},
        ]}
        showsVerticalScrollIndicator={false}>
        <Reveal style={styles.header}>
          <View style={styles.brandBadge}>
            <BrandLogo size={26} />
          </View>
          <View style={styles.brandText}>
            <Text style={styles.brandName}>اتوماسیون هوشمند رازی</Text>
            <Text style={styles.brandCompany}>بنیاد علوم کاربردی رازی</Text>
          </View>
          <SourcePill source={feed.source} />
        </Reveal>

        <Reveal delay={80}>
          <View style={styles.hero}>
            <Svg style={StyleSheet.absoluteFill} width="100%" height="100%">
              <Defs>
                <LinearGradient id="hero-bg" x1="0" y1="0" x2="0.8" y2="1">
                  <Stop offset="0" stopColor="#122748" />
                  <Stop offset="0.55" stopColor="#0C1B33" />
                  <Stop offset="1" stopColor="#081326" />
                </LinearGradient>
                <RadialGradient id="hero-glow" cx="82%" cy="8%" r="72%">
                  <Stop offset="0" stopColor="#2E7BFF" stopOpacity={0.4} />
                  <Stop offset="1" stopColor="#2E7BFF" stopOpacity={0} />
                </RadialGradient>
              </Defs>
              <Rect width="100%" height="100%" rx={radius.xl} fill="url(#hero-bg)" />
              <Rect width="100%" height="100%" rx={radius.xl} fill="url(#hero-glow)" />
            </Svg>

            <View style={styles.heroTop}>
              <View style={styles.heroTitleBlock}>
                <Text style={styles.eyebrow}>پایش زنده دما</Text>
                <View style={styles.heroTitleRow}>
                  <View style={[styles.channelDot, {backgroundColor: meta.color}]} />
                  <Text style={styles.heroTitle}>{meta.name}</Text>
                  <Text style={styles.heroRoom}>{meta.room}</Text>
                </View>
              </View>

              <View
                style={[
                  styles.statusChip,
                  {borderColor: `${accent}66`, backgroundColor: `${accent}1A`},
                ]}>
                <View style={[styles.statusDot, {backgroundColor: accent}]} />
                <Text style={[styles.statusText, {color: accent}]}>{trendLabel[trend]}</Text>
              </View>
            </View>

            <View style={styles.gaugeWrap}>
              <Gauge value={selected.temp} min={0} max={meta.max} color={meta.color} size={236}>
                <Text style={styles.gaugeValue}>{formatTemp(selected.temp)}</Text>
                <Text style={styles.gaugeUnit}>درجه سلسیوس</Text>
                <View style={styles.gaugeDelta}>
                  <Text style={[styles.gaugeDeltaText, {color: accent}]}>
                    {formatDelta(selected.delta)}°
                  </Text>
                  <Text style={styles.gaugeDeltaHint}>در ۱۰ ثانیه</Text>
                </View>
              </Gauge>
            </View>

            <View style={styles.statsRow}>
              <View style={styles.statCell}>
                <Text style={styles.statLabel}>کمینه</Text>
                <Text style={styles.statValue}>{formatTemp(stats.min)}°</Text>
              </View>
              <View style={[styles.statCell, styles.statCellMiddle]}>
                <Text style={styles.statLabel}>میانگین</Text>
                <Text style={styles.statValue}>{formatTemp(stats.avg)}°</Text>
              </View>
              <View style={styles.statCell}>
                <Text style={styles.statLabel}>بیشینه</Text>
                <Text style={styles.statValue}>{formatTemp(stats.max)}°</Text>
              </View>
            </View>
          </View>
        </Reveal>

        <Reveal delay={160} style={styles.sectionHead}>
          <Text style={styles.sectionTitle}>کانال‌های اندازه‌گیری</Text>
          <Text style={styles.sectionHint}>برای مشاهده جزئیات لمس کنید</Text>
        </Reveal>

        <Reveal delay={220} style={styles.grid}>
          {feed.channels.map(channel => (
            <View key={channel.id} style={{width: cardWidth}}>
              <ChannelCard
                reading={channel}
                active={channel.id === selected.id}
                onPress={() => setSelectedId(channel.id)}
              />
            </View>
          ))}
        </Reveal>

        <Reveal delay={300}>
          <View style={styles.chartCard}>
            <View style={styles.chartHeader}>
              <View>
                <Text style={styles.cardTitle}>نمودار دما</Text>
                <Text style={styles.cardSubtitle}>روند کانال‌های فعال</Text>
              </View>
            </View>

            <View style={styles.windowRow}>
              {WINDOWS.map(item => {
                const active = item.sec === windowSec;
                return (
                  <Pressable
                    key={item.sec}
                    onPress={() => setWindowSec(item.sec)}
                    style={[styles.windowChip, active && styles.windowChipActive]}>
                    <Text style={[styles.windowText, active && styles.windowTextActive]}>
                      {item.label}
                    </Text>
                  </Pressable>
                );
              })}
            </View>

            <TemperatureChart
              series={series}
              windowSec={windowSec}
              width={chartWidth}
              now={feed.lastUpdate}
            />

            <View style={styles.legend}>
              {feed.channels.map(channel => {
                const off = hidden.includes(channel.id);
                return (
                  <Pressable
                    key={channel.id}
                    onPress={() => toggleSeries(channel.id)}
                    style={[
                      styles.legendChip,
                      !off && {borderColor: `${channel.color}66`, backgroundColor: `${channel.color}12`},
                    ]}>
                    <View
                      style={[
                        styles.legendDot,
                        {backgroundColor: off ? colors.muted : channel.color},
                      ]}
                    />
                    <Text style={[styles.legendText, off && {color: colors.muted}]}>
                      {channel.name}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
          </View>
        </Reveal>

        <Reveal delay={380} style={styles.footer}>
          <Text style={styles.footerText}>
            {feed.note ?? 'داده زنده از سرور پایش دما'}
          </Text>
          <Text style={styles.footerTime}>
            آخرین به‌روزرسانی: {formatClock(feed.lastUpdate)}
          </Text>
        </Reveal>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {flex: 1, backgroundColor: colors.bg},
  scroll: {
    paddingHorizontal: SCREEN_PADDING,
    paddingBottom: space(10),
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: space(5),
  },
  brandBadge: {
    width: 44,
    height: 44,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(46, 123, 255, 0.12)',
    borderWidth: 1,
    borderColor: colors.borderStrong,
  },
  brandText: {flex: 1, marginLeft: space(3)},
  brandName: {color: colors.text, fontSize: 15.5, fontWeight: '800'},
  brandCompany: {
    color: colors.muted,
    fontSize: 10.5,
    marginTop: 2,
    letterSpacing: 0.5,
  },
  pill: {
    flexDirection: 'row',
    alignItems: 'center',
    borderWidth: 1,
    borderRadius: radius.pill,
    paddingHorizontal: space(2.5),
    paddingVertical: space(1.5),
    backgroundColor: 'rgba(5, 10, 23, 0.6)',
  },
  pillDot: {width: 6, height: 6, borderRadius: 3, marginRight: space(1.5)},
  pillText: {fontSize: 10.5, fontWeight: '700'},
  hero: {
    borderRadius: radius.xl,
    borderWidth: 1,
    borderColor: colors.border,
    paddingHorizontal: space(5),
    paddingTop: space(5),
    paddingBottom: space(4),
    overflow: 'hidden',
    ...shadow.card,
  },
  heroTop: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
  },
  heroTitleBlock: {flex: 1},
  eyebrow: {color: colors.muted, fontSize: 10.5, letterSpacing: 1.2},
  heroTitleRow: {flexDirection: 'row', alignItems: 'center', marginTop: space(2)},
  channelDot: {width: 9, height: 9, borderRadius: 4.5, marginRight: space(2)},
  heroTitle: {color: colors.text, fontSize: 18, fontWeight: '800'},
  heroRoom: {color: colors.muted, fontSize: 11, marginLeft: space(2), marginTop: 3},
  statusChip: {
    flexDirection: 'row',
    alignItems: 'center',
    borderWidth: 1,
    borderRadius: radius.pill,
    paddingHorizontal: space(2.5),
    paddingVertical: space(1.5),
  },
  statusDot: {width: 6, height: 6, borderRadius: 3, marginRight: space(1.5)},
  statusText: {fontSize: 10, fontWeight: '700'},
  gaugeWrap: {alignItems: 'center', marginTop: space(2)},
  gaugeValue: {
    ...tabular,
    color: colors.text,
    fontSize: 52,
    fontWeight: '800',
    letterSpacing: -1.8,
  },
  gaugeUnit: {color: colors.muted, fontSize: 11, marginTop: -space(1)},
  gaugeDelta: {flexDirection: 'row', alignItems: 'center', marginTop: space(2.5)},
  gaugeDeltaText: {...tabular, fontSize: 14, fontWeight: '800'},
  gaugeDeltaHint: {color: colors.muted, fontSize: 10, marginLeft: space(1.5)},
  statsRow: {
    flexDirection: 'row',
    marginTop: space(4),
    borderTopWidth: 1,
    borderTopColor: 'rgba(124, 176, 255, 0.1)',
    paddingTop: space(4),
  },
  statCell: {flex: 1, alignItems: 'center'},
  statCellMiddle: {
    borderLeftWidth: 1,
    borderRightWidth: 1,
    borderColor: 'rgba(124, 176, 255, 0.1)',
  },
  statLabel: {color: colors.muted, fontSize: 10},
  statValue: {...tabular, color: colors.text, fontSize: 16, fontWeight: '700', marginTop: space(1)},
  sectionHead: {
    flexDirection: 'row',
    alignItems: 'baseline',
    justifyContent: 'space-between',
    marginTop: space(7),
    marginBottom: space(3),
  },
  sectionTitle: {color: colors.text, fontSize: 14, fontWeight: '700'},
  sectionHint: {color: colors.muted, fontSize: 10},
  grid: {flexDirection: 'row', flexWrap: 'wrap', gap: CARD_GAP},
  chartCard: {
    marginTop: space(6),
    backgroundColor: colors.surface,
    borderRadius: radius.xl,
    borderWidth: 1,
    borderColor: colors.border,
    padding: space(4),
    overflow: 'hidden',
  },
  chartHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  cardTitle: {color: colors.text, fontSize: 14, fontWeight: '700'},
  cardSubtitle: {color: colors.muted, fontSize: 10.5, marginTop: 2},
  windowRow: {flexDirection: 'row', marginTop: space(3), marginBottom: space(2)},
  windowChip: {
    paddingHorizontal: space(3),
    paddingVertical: space(1.5),
    borderRadius: radius.pill,
    borderWidth: 1,
    borderColor: colors.border,
    marginRight: space(2),
    backgroundColor: 'rgba(124, 176, 255, 0.04)',
  },
  windowChipActive: {
    backgroundColor: 'rgba(46, 123, 255, 0.22)',
    borderColor: colors.brand,
  },
  windowText: {color: colors.textSoft, fontSize: 10.5, fontWeight: '600'},
  windowTextActive: {color: '#FFFFFF'},
  legend: {flexDirection: 'row', flexWrap: 'wrap', gap: space(2), marginTop: space(3)},
  legendChip: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: space(2.5),
    paddingVertical: space(1.5),
    borderRadius: radius.pill,
    borderWidth: 1,
    borderColor: colors.border,
  },
  legendDot: {width: 6, height: 6, borderRadius: 3, marginRight: space(1.5)},
  legendText: {color: colors.textSoft, fontSize: 10},
  footer: {marginTop: space(6), alignItems: 'center'},
  footerText: {color: colors.muted, fontSize: 10.5, textAlign: 'center'},
  footerTime: {...tabular, color: colors.muted, fontSize: 10, marginTop: space(1.5)},
});
