export type ChannelMeta = {
  id: number;
  name: string;
  room: string;
  color: string;
  /** Full-scale value of this channel's gauge. */
  max: number;
};

/**
 * The four measurement channels of a Razi furnace controller board. Names match
 * the web dashboard; every reading shown in the app comes from the controller.
 */
export const CHANNELS: ChannelMeta[] = [
  {id: 1, name: 'کوره ۱', room: 'سالن اصلی', color: '#FF7A45', max: 1200},
  {id: 2, name: 'کوره ۲', room: 'سالن شرقی', color: '#37D4FF', max: 1200},
  {id: 3, name: 'کوره ۳', room: 'سالن غربی', color: '#3EE0A5', max: 1200},
  {id: 4, name: 'کوره ۴', room: 'آزمایشگاه', color: '#B98CFF', max: 1200},
];

export const channelById = (id: number): ChannelMeta =>
  CHANNELS.find(channel => channel.id === id) ?? CHANNELS[0];
