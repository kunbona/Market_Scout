// 最小化的 plotly.js 类型声明 — 实际只在 CyclePage 用，简化版足够
declare module 'plotly.js-basic-dist-min' {
  const Plotly: any;
  export default Plotly;
}

declare module 'react-plotly.js' {
  import { Component } from 'react';
  export interface Figure {
    data: any[];
    layout?: any;
    frames?: any[];
  }
  export interface PlotParams {
    [key: string]: any;
  }
  export default class Plot extends Component<{ data: any[]; layout?: any; config?: any; frames?: any[]; style?: any; className?: string; onInitialized?: (figure: any, graphDiv: any) => void; onUpdate?: (figure: any, graphDiv: any) => void; useResizeHandler?: boolean }> {
    static newPlot(gd: HTMLElement, data: any[], layout?: any, config?: any): Promise<any>;
    static react(gd: HTMLElement, data: any[], layout?: any, config?: any): Promise<any>;
    static purge(gd: HTMLElement): void;
  }
}
