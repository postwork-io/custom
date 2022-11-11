#!/usr/bin/env python3

from __future__ import absolute_import
import os
import tempfile

from Deadline.Plugins import DeadlinePlugin, PluginType
from Deadline.Scripting import FileUtils, RepositoryUtils, SystemUtils
from six.moves import range


def GetDeadlinePlugin():
    return Cinema4DPlugin()

def CleanupDeadlinePlugin( deadlinePlugin ):
    deadlinePlugin.Cleanup()

class Cinema4DPlugin( DeadlinePlugin ):

    def __init__( self ):
        self.LocalRendering = False
        self.LocalFilePath = ""
        self.NetworkFilePath = ""
        self.LocalMPFilePath = ""
        self.NetworkMPFilePath = ""
        self.FinishedFrameCount = 0
        self.CheckProgress = False
        self.CurrentRenderPhase = ""
        self.UsingRedshift = False

        self.InitializeProcessCallback += self.InitializeProcess
        self.PreRenderTasksCallback += self.PreRenderTasks
        self.RenderExecutableCallback += self.RenderExecutable
        self.RenderArgumentCallback += self.RenderArgument
        self.PostRenderTasksCallback += self.PostRenderTasks

    def Cleanup( self ):
        for stdoutHandler in self.StdoutHandlers:
            del stdoutHandler.HandleCallback
        
        del self.InitializeProcessCallback
        del self.PreRenderTasksCallback
        del self.RenderExecutableCallback
        del self.RenderArgumentCallback
        del self.PostRenderTasksCallback

    def InitializeProcess( self ):
        self.StdoutHandling = True
        self.SingleFramesOnly = False
        self.PopupHandling = True
        self.UseProcessTree = True
        self.currFrame = None
        self.prevFrame = self.GetStartFrame()
        self.C4DExe = ""

        self.AddStdoutHandlerCallback( ".*Document not found.*" ).HandleCallback += self.HandleStdoutError
        self.AddStdoutHandlerCallback( ".*Project not found.*" ).HandleCallback += self.HandleStdoutError
        self.AddStdoutHandlerCallback( ".*Error rendering project.*" ).HandleCallback += self.HandleStdoutError
        self.AddStdoutHandlerCallback( ".*Error loading project.*" ).HandleCallback += self.HandleStdoutError
        self.AddStdoutHandlerCallback( ".*Error rendering document.*" ).HandleCallback += self.HandleStdoutError
        self.AddStdoutHandlerCallback( ".*Error loading document.*" ).HandleCallback += self.HandleStdoutError
        self.AddStdoutHandlerCallback( ".*Rendering failed.*" ).HandleCallback += self.HandleStdoutError
        self.AddStdoutHandlerCallback( ".*Asset missing.*" ).HandleCallback += self.HandleStdoutError
        self.AddStdoutHandlerCallback( ".*Invalid License.*" ).HandleCallback += self.HandleStdoutError
        self.AddStdoutHandlerCallback( ".*License Check error.*" ).HandleCallback += self.HandleStdoutError
        self.AddStdoutHandlerCallback( ".*Files cannot be written.*" ).HandleCallback += self.HandleStdoutError
        self.AddStdoutHandlerCallback( ".*Enter Registration Data.*" ).HandleCallback += self.HandleStdoutError
        self.AddStdoutHandlerCallback( ".*The output resolution is too high for the selected render engine.*" ).HandleCallback += self.HandleOutputResolutionError
        self.AddStdoutHandlerCallback( ".*Unable to write file.*" ).HandleCallback += self.HandleStdoutError
        self.AddStdoutHandlerCallback( r".*\[rlm\] abort_on_license_fail enabled.*" ).HandleCallback += self.HandleStdoutError

        self.AddStdoutHandlerCallback( ".*Rendering frame ([0-9]+) at.*" ).HandleCallback += self.HandleStdoutProgress
        self.AddStdoutHandlerCallback( ".*Rendering Phase: Setup.*" ).HandleCallback += self.HandleSetupProgress
        self.AddStdoutHandlerCallback( ".*Rendering Phase: Main Render.*" ).HandleCallback += self.HandleProgressCheck
        self.AddStdoutHandlerCallback( ".*Progress: (\d+)%.*" ).HandleCallback += self.HandleTaskProgress 
        self.AddStdoutHandlerCallback( ".*Rendering successful.*" ).HandleCallback += self.HandleProgress2
        self.AddStdoutHandlerCallback( ".*Rendering Phase: Finalize.*" ).HandleCallback += self.HandleFrameProgress

        #Redshift progress handling
        self.AddStdoutHandlerCallback( ".*Redshift Info.*|.*Redshift Detailed.*|.*Redshift Debug.*|.*Redshift Warning.*|.*Redshift Error.*" ).HandleCallback += self.HandleUsingRedshift
        self.AddStdoutHandlerCallback( r"Frame rendering aborted." ).HandleCallback += self.HandleStdoutError
        self.AddStdoutHandlerCallback( r"Rendering was internally aborted" ).HandleCallback += self.HandleStdoutError
        self.AddStdoutHandlerCallback( r'Cannot find procedure "rsPreference"' ).HandleCallback += self.HandleStdoutError
        self.AddStdoutHandlerCallback(
            "Rendering frame \\d+ \\((\\d+)/(\\d+)\\)" ).HandleCallback += self.HandleRedshiftNewFrameProgress
        self.AddStdoutHandlerCallback(
            "Block (\\d+)/(\\d+) .+ rendered" ).HandleCallback += self.HandleRedshiftBlockRendered

        self.AddStdoutHandlerCallback( ".*ImportError: No module named site.*" ).HandleCallback += self.HandleNoSite
        self.AddStdoutHandlerCallback( ".*code for hash .* was not found." ).HandleCallback += self.HandleHashNotFound

        # Handle QuickTime popup dialog
        # "QuickTime does not support the current Display Setting.  Please change it and restart this application."
        self.AddPopupHandler( "Unsupported Display", "OK" )
        self.AddPopupHandler( "Nicht.*", "OK" )

        self.AddPopupHandler( ".*Render history settings.*", "OK" )

    def PreRenderTasks( self ):
        self.LogInfo("Starting Cinema 4D Task")
        self.FinishedFrameCount = 0

    def RenderExecutable( self ):
        self.version = self.GetIntegerPluginInfoEntryWithDefault( "Version", 18 ) 
        self.C4DExe = self.GetRenderExecutable( "C4D_" + str(self.version) + "_RenderExecutable" , "Cinema 4D %s" %self.version )

        return self.C4DExe

    def setDefaultPluginSearchpath(self):
        """
        Cinema 4D R23 and S24 have a known bug which causes the commandline executable to not find the default plugins directory.
        As a workaround this function adds the default plugin directory to the g_additionalModulePath so it can be found.
        See: https://support.maxon.net/kb/faq.php?id=115 or https://support.maxon.net/hc/en-us/articles/1500006331481-Plug-ins-not-found-in-CommandLine-Render
        for additional info about the bug and other workarounds.
        """
        
        parent_dir = os.path.dirname(self.C4DExe)
        if SystemUtils.IsRunningOnMac():
            # On MacOS executable will be at '/Applications/MAXON CINEMA 4D R24/Commandline.app/Contents/MacOS/Commandline'.
            # The 'plugins' folder is located 3 levels higher, together with 'Commandline.app'
            parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(parent_dir)))

        defaultPluginsDir = os.path.join(parent_dir, 'plugins')

        if not os.path.exists(defaultPluginsDir):
            self.LogWarning('Failed to find Default plugins directory (%s).  If Cinema 4D is not able to access the default plugins directory it may render incorrectly.' % defaultPluginsDir)
            return
        
        existingPluginsDirs = self.GetEnvironmentVariable( 'g_additionalModulePath' )
        if not existingPluginsDirs:
            existingPluginsDirs = os.environ.get('g_additionalModulePath', "")

        if defaultPluginsDir in existingPluginsDirs:
            return
        
        #Join the lists filtering out empty paths
        finalPluginsDirs = ';'.join([_f for _f in [existingPluginsDirs, defaultPluginsDir] if _f])

        self.SetEnvironmentVariable('g_additionalModulePath', finalPluginsDirs)

    def RenderArgument( self ):
        if SystemUtils.IsRunningOnLinux() and self.GetBooleanConfigEntryWithDefault( "SetLinuxEnvironment", True ):
            c4dDir = os.path.dirname( self.C4DExe )
            ldPath = os.environ.get( "LD_LIBRARY_PATH", "" )
            pyPath = os.environ.get( "PYTHONPATH", "" )
            path = os.environ.get( "PATH", "" )
            
            modLdPath = "%s/../lib64:%s/resource/modules/python/Python.linux64.framework/lib64:%s/resource/modules/embree.module/libs/linux64:%s" % ( c4dDir, c4dDir, c4dDir, ldPath )
            modPyPath = "%s/resource/modules/python/Python.linux64.framework/lib/python2.7:%s/resource/modules/python/Python.linux64.framework/lib64/python2.7/lib-dynload:%s" % ( c4dDir, c4dDir, pyPath )
            modPath = "%s:%s" % ( path, c4dDir )
            
            self.LogInfo( "[LD_LIBRARY_PATH] set to %s" % modLdPath )
            self.LogInfo( "[PYTHONPATH] set to %s" % modPyPath )
            self.LogInfo( "[PATH] set to %s" % modPath )
            
            self.SetEnvironmentVariable( "LD_LIBRARY_PATH", modLdPath )
            self.SetEnvironmentVariable( "PYTHONPATH", modPyPath )
            self.SetEnvironmentVariable( "PATH", modPath )
            
        if self.version in [23, 24]:
            self.setDefaultPluginSearchpath()
        
        sceneFile = self.GetPluginInfoEntryWithDefault( "SceneFile", self.GetDataFilename() )
        sceneFile = RepositoryUtils.CheckPathMapping( sceneFile )
        sceneFile = self.ProcessPath( sceneFile )
        
        activeTake = self.GetPluginInfoEntryWithDefault( "Take", "" )
        argument = [ "-nogui" ]
        
        # NOTE: Negate the "No OpenGL" plugin info option from the monitor submitter to make the logic below
        # easier to read
        self.loadOpenGL = not self.GetBooleanPluginInfoEntryWithDefault( "NoOpenGL", False )

        # If the integrated submitter has specified a renderer other than Hardware OpenGL
        # we can skip loading OpenGL
        renderer = self.GetPluginInfoEntryWithDefault( "Renderer", "" )
        if self.loadOpenGL and renderer not in ( "", "ogl_hardware" ):
            self.loadOpenGL = False

        if not self.loadOpenGL:
            argument.append( "-noopengl" )

        # When Cinema4D starts, it will acquire a floating Redshift license if one is available. 
        # This is done even if Redshift is not the current renderer, and even if there is a node-locked Redshift license already on that machine.
        # The following lets the user set a System Environment Variable REDSHIFT_LICENSE_MAXON_DISABLE=True to disable the acquisition of a 
        # floating Redshift license on machines with a node-locked license. 
        # The same code will also disable the acquisition of a floating Redshift license if the current renderer is not Redshift, 
        # unless the renderer is unknown, e.g. because the submission came from the Monitor submitter:
        if os.environ.get( "REDSHIFT_LICENSE_MAXON_DISABLE", "False" ) == "True" or (renderer != "redshift" and renderer != ""):
            argument.append("-redshift-license-maxon-disable")

        exportJob = "Export" in renderer

        if not exportJob or renderer == "ArnoldExport":
            argument.append("-arnoldAbortOnLicenseFail %s" % str(self.GetBooleanConfigEntryWithDefault("AbortOnArnoldLicenseFail", True)).lower())

        if not exportJob:
            argument.append( '-render "%s"' % sceneFile )
            argument.append( '-frame %s %s' % ( self.GetStartFrame(), self.GetEndFrame() ) )
            if self.GetBooleanPluginInfoEntryWithDefault( "EnableFrameStep", False ):
                argument.append( self.GetPluginInfoEntryWithDefault( "FrameStep", "2" ) )
         
            if activeTake and self.GetIntegerPluginInfoEntryWithDefault( "Version", 17 ) >= 17:
                argument.append( '-take "%s"' % activeTake )
            
            threads = self.GetNumThreads()
            if threads > 0:
                argument.append( "-threads %s" % threads )
            
            width = self.GetIntegerPluginInfoEntryWithDefault( "Width", 0 )
            height = self.GetIntegerPluginInfoEntryWithDefault( "Height", 0 )
            if width and height:
                argument.append( "-oresolution %s %s" % ( width, height ) )

            selectedGPUs = self.GetGpuOverrides()
            if selectedGPUs:
                for gpu in selectedGPUs:
                    argument.append( "-redshift-gpu %s" % gpu )

            self.LocalRendering = self.GetBooleanPluginInfoEntryWithDefault( "LocalRendering", False )
            # Build the output filename from the path and prefix
            filepath = self.GetPluginInfoEntryWithDefault( "FilePath", "" ).strip()
            filepath = RepositoryUtils.CheckPathMapping( filepath )
            if filepath:
                filepath = self.ProcessPath( filepath )
                
                if self.LocalRendering:
                    self.NetworkFilePath, postTokens = self.SplitTokens( filepath )
                    self.ValidateFilepath( self.NetworkFilePath )
                    
                    filepath = self.CreateTempDirectory( "c4dOutput" )
                    filepath = self.ProcessPath( filepath )
                    
                    self.LocalFilePath = filepath
                    self.ValidateFilepath( self.LocalFilePath )
                    
                    filepath = os.path.join( filepath, postTokens )
                    filepath = self.ProcessPath( filepath )
                    
                    self.LogInfo( "Rendering main output to local drive, will copy files and folders to final location after render is complete" )
                else:
                    pathBeforeTokens, _ = self.SplitTokens( filepath )
                    self.ValidateFilepath( pathBeforeTokens )

                    self.LogInfo( "Rendering main output to network drive" )

                fileprefix = self.GetPluginInfoEntryWithDefault( "FilePrefix", "" ).strip()
                argument.append( '-oimage "%s"' % os.path.join( filepath, fileprefix ) )
            
            # Build the multipass output filename from the path and prefix
            multifilepath = self.GetPluginInfoEntryWithDefault( "MultiFilePath", "" ).strip()
            multifilepath = RepositoryUtils.CheckPathMapping( multifilepath )
            if multifilepath:
                multifilepath = self.ProcessPath( multifilepath )

                if self.LocalRendering:
                    self.NetworkMPFilePath, postTokens = self.SplitTokens( multifilepath )
                    self.ValidateFilepath( self.NetworkMPFilePath )

                    multifilepath = self.CreateTempDirectory( "c4dOutputMP" )
                    multifilepath = self.ProcessPath( multifilepath )
                    
                    self.LocalMPFilePath = multifilepath
                    self.ValidateFilepath( self.LocalMPFilePath )
                    
                    multifilepath = os.path.join( multifilepath, postTokens )
                    multifilepath = self.ProcessPath( multifilepath )

                    self.LogInfo( "Rendering multipass output to local drive, will copy files and folders to final location after render is complete" )
                else:
                    pathBeforeTokens, _ = self.SplitTokens( multifilepath )
                    self.ValidateFilepath( pathBeforeTokens )

                    self.LogInfo( "Rendering multipass output to network drive" )
            
                multifileprefix = self.GetPluginInfoEntryWithDefault( "MultiFilePrefix", "" ).strip()
                argument.append( '-omultipass "%s"' % os.path.join( multifilepath, multifileprefix ) )

            redshiftLogVerbosity = self.GetConfigEntryWithDefault( "RedshiftLogging", "Debug" )
            if redshiftLogVerbosity != "None":
                argument.append( "-redshift-log-console %s" % redshiftLogVerbosity )
        
        elif renderer == "ArnoldExport":
            if activeTake and self.GetIntegerPluginInfoEntryWithDefault( "Version", 17 ) >= 17:
                argument.append( '-take "%s"' % activeTake )

            argument.append( '-arnoldAssExport' )
            # Arnold needs the rest of its command-line args to be bundled together inside quotes for their export
            # so that they can parse them out from the string, without needing to add more actual command-line flags.
            arnoldExportArgs = [ 'scene=%s' % sceneFile ]
            exportFile = self.ProcessPath( self.GetPluginInfoEntryWithDefault( "ExportFile", "" ) )
            exportFile = RepositoryUtils.CheckPathMapping(exportFile)
            if exportFile:
                self.ValidateFilepath( os.path.dirname( exportFile ) )
                arnoldExportArgs.append( "filename=%s" % exportFile )

            arnoldExportArgs.append( 'startFrame=%s' % self.GetStartFrame() )
            arnoldExportArgs.append( 'endFrame=%s' % self.GetEndFrame() )

            argument.append( '"%s"' % ";".join( arnoldExportArgs ) )
        elif renderer == "OctaneExport":
            octaneExportArgs = []

            exportFile = self.ProcessPath( self.GetPluginInfoEntryWithDefault( "ExportFile", "" ) )
            exportFile = RepositoryUtils.CheckPathMapping(exportFile)
            if exportFile:
                self.ValidateFilepath( os.path.dirname( exportFile ) )
                octaneExportArgs.append( '"%s"' % sceneFile )
                octaneExportArgs.append( '-exportORBX' )
                octaneExportArgs.append( '"%s"' % exportFile )
            else:
                self.FailRender( "Cannot complete an 'Export Job' when an 'Export File' has not been specified" )
            
            argument.extend( octaneExportArgs ) 

        return " ".join( argument )
    
    def GetNumThreads( self ):
        """
        Returns the number of threads we want to use based off the number of threads specified in the job and the Worker's CPU Affinity
        :return: The number of threads
        """
        threads = self.GetIntegerPluginInfoEntryWithDefault( "Threads", 0 )

        #OverrideCpuAffinity - Returns whether the Worker has its CPU affinity override enabled
        if self.OverrideCpuAffinity():
            #CPUAffinity - returns a list containing the indices of all CPUs the Worker has in its affinity
            affinity = len( self.CpuAffinity() )
            if threads == 0:
                threads = affinity
            else:
                threads = min( affinity, threads )
                
        return threads
    
    def PostRenderTasks( self ):
        if( self.LocalRendering ):
            if( self.NetworkFilePath != "" ):
                self.LogInfo( "Moving main output files and folders from " + self.LocalFilePath + " to " + self.NetworkFilePath )
                self.VerifyAndMoveDirectory( self.LocalFilePath, self.NetworkFilePath, False, -1 )
            if( self.NetworkMPFilePath != "" ):
                self.LogInfo( "Moving multipass output files and folders from " + self.LocalMPFilePath + " to " + self.NetworkMPFilePath )
                self.VerifyAndMoveDirectory( self.LocalMPFilePath, self.NetworkMPFilePath, False, -1 )
        
        self.LogInfo( "Finished Cinema 4D Task" )

    def ProcessPath( self, filepath ):
        if SystemUtils.IsRunningOnWindows():
            filepath = filepath.replace( "/", "\\" )
            if filepath.startswith( "\\" ) and not filepath.startswith( "\\\\" ):
                filepath = "\\" + filepath
        else:
            filepath = filepath.replace( "\\", "/" )
        return filepath

    def GetGpuOverrides( self ):
         # If the number of gpus per task is set, then need to calculate the gpus to use.
        gpusPerTask = self.GetIntegerPluginInfoEntryWithDefault( "GPUsPerTask", 0 )
        gpusSelectDevices = self.GetPluginInfoEntryWithDefault( "GPUsSelectDevices", "" )
        resultGPUs = []

        if self.OverrideGpuAffinity():
            overrideGPUs = self.GpuAffinity()
            if gpusPerTask == 0 and gpusSelectDevices != "":
                gpus = gpusSelectDevices.split( "," )
                notFoundGPUs = []
                for gpu in gpus:
                    if int( gpu ) in overrideGPUs:
                        resultGPUs.append( gpu )
                    else:
                        notFoundGPUs.append( gpu )
                
                if len( notFoundGPUs ) > 0:
                    self.LogWarning( "The Worker is overriding its GPU affinity and the following GPUs do not match the Workers affinity so they will not be used: " + ",".join( notFoundGPUs ) )
                if len( resultGPUs ) == 0:
                    self.FailRender( "The Worker does not have affinity for any of the GPUs specified in the job." )
            elif gpusPerTask > 0:
                if gpusPerTask > len( overrideGPUs ):
                    self.LogWarning( "The Worker is overriding its GPU affinity and the Worker only has affinity for " + str( len( overrideGPUs ) ) + " Workers of the " + str( gpusPerTask ) + " requested." )
                    resultGPUs =  overrideGPUs
                else:
                    resultGPUs = list( overrideGPUs )[:gpusPerTask]
            else:
                resultGPUs = overrideGPUs
        elif gpusPerTask == 0 and gpusSelectDevices != "":
            resultGPUs = gpusSelectDevices.split( "," )

        elif gpusPerTask > 0:
            gpuList = []
            for i in range( ( self.GetThreadNumber() * gpusPerTask ), ( self.GetThreadNumber() * gpusPerTask ) + gpusPerTask ):
                gpuList.append( str( i ) )
            resultGPUs = gpuList
        
        resultGPUs = list( resultGPUs )
        
        return resultGPUs

    def SplitTokens( self, filePath ):
        """
        This function is used to split output file paths to contain 2 parts the first of which will contain no tokens.
        This splits output paths such as c:\path\to\location\$take\$camera\filename to "c:\path\to\location " and "$take\$camera\filename"
        we are doing this so that we keep the full output paths since we are unable to resolves the tokens in our plugin.
        :param filePath: the filepath to split the tokens out of
        :return: a tuple, first entry making up the path before any tokens and the second entry is the path containing tokens
        """
        if "$" not in filePath:
            return filePath, ""
        
        pathSeparator  = -1
        filePath = filePath.replace( "\\", "/" )
        tokenStartIndex = filePath.find( "$" )
        if tokenStartIndex != -1:
            pathSeparator = filePath.rfind( "/", 0, tokenStartIndex )
        
        if pathSeparator == -1:
            return "", filePath
        
        preTokenPath = filePath[:pathSeparator]
        postTokenPath = filePath[( pathSeparator+1):]
        return preTokenPath, postTokenPath

    def ValidateFilepath( self, directory ):
        self.LogInfo( "Validating the path: '%s'" % directory )

        if not os.path.exists( directory ):
            try:
                os.makedirs( directory )
            except:
                self.FailRender( "Failed to create path: '%s'" % directory )

        # Test to see if we have permission to create a file
        try:
            # TemporaryFile deletes the "file" when it closes, we only care that it can be created
            with tempfile.TemporaryFile( dir=directory ) as tempFile:
                pass
        except:
            self.FailRender( "Failed to create test file in directory: '%s'" % directory )

    def HandleSetupProgress( self ):
        # If frame number is given update the Render status with the current frame
        if self.currFrame != None:
            self.CurrentRenderPhase = "Frame: " + str(self.currFrame) + ",  Rendering Phase: Setup"
        else:
            self.CurrentRenderPhase = "Rendering Phase: Setup"

    def HandleProgressCheck( self ):
        self.CheckProgress = True
        
        # If frame number is given update the Render status with the current frame
        if self.currFrame != None:
            self.CurrentRenderPhase = "Frame: " + str(self.currFrame) + ",  Rendering Phase: Main Render"
        else:
            self.CurrentRenderPhase = "Rendering Phase: Main Render"

    def HandleTaskProgress( self ):
        startFrame = self.GetStartFrame()
        endFrame = self.GetEndFrame()
        frameCount = abs( endFrame - startFrame ) + 1

        # Sometimes progress is reported as over 100%. We don't know why, but we're handling it here.
        subProgress = 1
        if float( self.GetRegexMatch( 1 ) ) <= 100:
            subProgress = float( self.GetRegexMatch( 1 ) ) / 100
        
        if self.currFrame != None and self.CheckProgress:
            
            if( self.prevFrame + subProgress ) < self.currFrame:
                self.prevFrame = self.currFrame
                progress = 100 * ( self.currFrame - startFrame ) // frameCount
                self.SetProgress( progress )
            else:
                progress = int( 100 * float( self.currFrame + subProgress - startFrame ) / float( frameCount ) )
                self.SetProgress( progress )
        
        # Update the 'Task Render Status' with the progress of each Render Phase
        self.SetStatusMessage( str( self.CurrentRenderPhase ) + " - Progress: " + str( self.GetRegexMatch( 1 ) ) + "%" )

    def HandleStdoutProgress( self ):
        self.currFrame = int(self.GetRegexMatch(1))
        self.SetStatusMessage(self.GetRegexMatch(0))

    def HandleProgress2( self ):
        self.SetProgress( 100 )
        self.SetStatusMessage( self.GetRegexMatch( 0 ) )

    def HandleFrameProgress( self ):
        self.FinishedFrameCount = self.FinishedFrameCount + 1
        self.CheckProgress = self.UsingRedshift

        # If frame number is given update the Render status with the current frame
        if self.currFrame is not None:
            self.CurrentRenderPhase = "Frame: " + str(self.currFrame) + ",  Rendering Phase: Finalize"
        else:
            self.CurrentRenderPhase = "Rendering Phase: Finalize"
        
        startFrame = self.GetStartFrame()
        endFrame = self.GetEndFrame()
        frameCount = abs( endFrame - startFrame ) + 1
        progress = 100 * self.FinishedFrameCount / frameCount

        self.SetProgress( progress )
        self.LogInfo( "Task Overall Progress: " + str( progress ) + "%" )

    def HandleNoSite( self ):
        self.FailRender( "Failed to import the following modules: site\nPlease ensure that your environment is set correctly or that you are allowing Deadline to set the render environment.\nPlease go to the C4D FAQ in the Deadline documentation for more information." )

    def HandleHashNotFound( self ):
        self.LogInfo( "OpenSSL has not been set up to work properly with C4D Batch, this is a non-blocking issue.\nPlease go to the C4D FAQ in the Deadline documentation for more information." )

    def HandleOutputResolutionError( self ):
        errorMsg = self.GetRegexMatch( 0 )
        if not self.loadOpenGL:
            errorMsg = "This job was configured to not load OpenGL. If you are using the Hardware OpenGL renderer, resubmit without the \"Don't Load OpenGL\" option checked."
        self.FailRender( errorMsg )

    def HandleStdoutError( self ):
        self.FailRender( self.GetRegexMatch( 0 ) )

    def HandleUsingRedshift( self ):
        self.UsingRedshift = True

    def HandleRedshiftNewFrameProgress( self ):
        self.FinishedFrameCount = float( self.GetRegexMatch(1) ) - 1
        startFrame = self.GetStartFrame()
        endFrame = self.GetEndFrame()
        frameCount = abs( endFrame - startFrame ) + 1

        progress = 100 * self.FinishedFrameCount / frameCount
        self.deadlinePlugin.SetProgress( progress )

    def HandleRedshiftBlockRendered( self ):
        startFrame = self.GetStartFrame()
        endFrame = self.GetEndFrame()
        frameCount = abs( endFrame - startFrame ) + 1

        completedBlockNumber = float( self.GetRegexMatch(1) )
        totalBlockCount = float( self.GetRegexMatch(2) )
        finishedFrames = completedBlockNumber / totalBlockCount
        finishedFrames = finishedFrames + self.FinishedFrameCount

        progress = 100 * finishedFrames / frameCount
        self.SetProgress( progress )