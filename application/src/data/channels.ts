export type ChannelMeta = {
  id: number;
  name: string;
  room: string;
  color: string;
  /** Typical working temperature used by the built-in demo generator. */
  baseTemp: number;
  max: number;
};

/**
 * The four measurement channels of a Razi furnace controller board.
 * Names match the web dashboard; the demo generator uses `baseTemp` so the
 * simulated curves look like real firings instead of noise.
 */
export const CHANNELS: ChannelMeta[] = [
  {id: 1, name: 'کوره ۱', room: 'سالن اصلی', color: '#FF7A45', baseTemp: 862, max: 1200},
  {id: 2, name: 'کوره ۲', room: 'سالن شرقی', color: '#37D4FF', baseTemp: 641, max: 1200},
  {id: 3, name: 'کوره ۳', room: 'سالن غربی', color: '#3EE0A5', baseTemp: 418, max: 1200},
  {id: 4, name: 'کوره ۴', room: 'آزمایشگاه', color: '#B98CFF', baseTemp: 1034, max: 1200},
];

export const channelById = (id: number): ChannelMeta =>
  CHANNELS.find(channel => channel.id === id) ?? CHANNELS[0];
