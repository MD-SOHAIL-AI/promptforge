export interface SequencedRunProjection{run:{run_id:string};last_sequence:number}
export function mergeWorkspaceProjection<T extends SequencedRunProjection>(current:T|null,next:T):T{if(!current||current.run.run_id!==next.run.run_id)return next;if(next.last_sequence<current.last_sequence)return current;return next}
