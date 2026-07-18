"use client";
import{useCallback,useEffect,useState}from"react";
import{deriveSurfacePhase,readControlPlane}from"@/lib/control-plane";
export function useControlPlaneResource<T>(path:string){const[data,setData]=useState<T|null>(null);const[loading,setLoading]=useState(true);const[error,setError]=useState<string|null>(null);const refresh=useCallback(async()=>{setLoading(true);try{setData(await readControlPlane<T>(path));setError(null)}catch(value){setError(value instanceof Error?value.message:"Backend unavailable")}finally{setLoading(false)}},[path]);useEffect(()=>{void refresh()},[refresh]);return{data,loading,error,phase:deriveSurfacePhase({hasData:data!==null,loading,error:error!==null}),refresh}}
