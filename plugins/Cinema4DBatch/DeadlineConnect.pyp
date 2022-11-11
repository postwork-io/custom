from __future__ import absolute_import
from __future__ import print_function
import errno
from io import open
import ntpath
import os
import sys
import traceback

import c4d
from c4d import documents

try:
    import socket
except:
    pass

try:
    import struct
except:
    pass

try:
    import subprocess
except:
    pass

try:
    unicode_type = unicode
except:
    unicode_type = str

# Executes a given Python script
# Syntax: cinema4d.exe "-DeadlineConnect Port AuthenticationToken"
deadlineSocket = None
isVerbose = False


def DeadlineConnect(arg):
    global deadlineSocket
    global isVerbose
    # Parse arguments
    argComponents = arg.split(' ')
    port = argComponents[1]
    authenticationToken = argComponents[2]
    errorFile = " ".join(argComponents[3:])
    errorFile = errorFile.strip("'")

    checkImportErrors(errorFile)

    HOSToutgoing = 'localhost'
    PORToutgoing = int(port)  # The same port as used by the server
    deadlineSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    deadlineSocket.connect((HOSToutgoing, PORToutgoing))
    send_msg(deadlineSocket, "TOKEN:" + authenticationToken)
    while 1:
        data = recv_msg(deadlineSocket)
        if not data:
            break
        if data.startswith("Verbose:"):
            try:
                isVerbose = bool(data[8:])
                send_msg(deadlineSocket, "SUCCESS: Set Verbose to %s" % isVerbose)
            except:
                print(traceback.format_exc())
                send_msg(deadlineSocket, "ERROR: Failed to set Verbose.")

        elif data.startswith("DeadlineStartup:"):
            scene = data[16:]
            print("Loading Scene: " + scene)
            if sys.version_info[0] < 3 and isinstance(scene, unicode):
                scene = toBytes(scene)

            if loadScene(scene):
                send_msg(deadlineSocket, "SUCCESS: Loaded Scene")
            else:
                send_msg(deadlineSocket, "ERROR: Unable to Load Scene")

        elif data.startswith("Pathmap:"):
            print("Running Path Mapping")
            try:
                args = data[8:]
                splitArgs = args.split(";")

                deadlineTemp = splitArgs[0]
                texPathFilename = None
                if len(splitArgs) > 1:
                    texPathFilename = splitArgs[1]

                runPathMapping(deadlineTemp, texPathFilename)
                send_msg(deadlineSocket, "SUCCESS: Done Path Mapping")
            except:
                print(traceback.format_exc())
                send_msg(deadlineSocket, "ERROR: Failed to Run Script")

        elif data.startswith("RunScript:"):
            script = data[10:]
            print("Running Script: " + script)
            try:
                runScript(script)
                send_msg(deadlineSocket, "SUCCESS: Script Ran Successfully")
            except:
                print(traceback.format_exc())
                send_msg(deadlineSocket, "ERROR: Failed to Run Script")

        elif data.startswith("EndJob"):
            send_msg(deadlineSocket, "SUCCESS: Closing Cinema4D")
            break
        else:
            send_msg(deadlineSocket, "ERROR: Unknown Command: " + data)


def GetDeadlineCommand():
    deadlineBin = ""
    try:
        deadlineBin = os.environ['DEADLINE_PATH']
    except KeyError:
        # if the error is a key error it means that DEADLINE_PATH is not set.
        # However Deadline command may be in the PATH or (on OSX) it could be in the file /Users/Shared/Thinkbox/DEADLINE_PATH
        pass

    # On OSX, we look for the DEADLINE_PATH file if the environment variable does not exist.
    if deadlineBin == "" and os.path.exists("/Users/Shared/Thinkbox/DEADLINE_PATH"):
        with open("/Users/Shared/Thinkbox/DEADLINE_PATH") as f:
            deadlineBin = f.read().strip()

    deadlineCommand = os.path.join(deadlineBin, "deadlinecommand")

    return deadlineCommand


def CallDeadlineCommand(arguments, hideWindow=True):
    deadlineCommand = GetDeadlineCommand()
    startupinfo = None
    creationflags = 0
    if os.name == 'nt':
        if hideWindow:
            # Python 2.6 has subprocess.STARTF_USESHOWWINDOW, and Python 2.7 has subprocess._subprocess.STARTF_USESHOWWINDOW, so check for both.
            if hasattr(subprocess, '_subprocess') and hasattr(subprocess._subprocess, 'STARTF_USESHOWWINDOW'):
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess._subprocess.STARTF_USESHOWWINDOW
            elif hasattr(subprocess, 'STARTF_USESHOWWINDOW'):
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        else:
            # still show top-level windows, but don't show a console window
            CREATE_NO_WINDOW = 0x08000000  # MSDN process creation flag
            creationflags = CREATE_NO_WINDOW

    environment = {}
    for key in os.environ.keys():
        environment[key] = str(os.environ[key])

    # Need to set the PATH, cuz windows seems to load DLLs from the PATH earlier that cwd....
    if os.name == 'nt':
        deadlineCommandDir = os.path.dirname(deadlineCommand)
        if not deadlineCommandDir == "":
            environment['PATH'] = deadlineCommandDir + os.pathsep + os.environ['PATH']

    arguments.insert(0, deadlineCommand)

    # Specifying PIPE for all handles to workaround a Python bug on Windows. The unused handles are then closed immediatley afterwards.
    proc = subprocess.Popen(arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            startupinfo=startupinfo, env=environment, creationflags=creationflags)
    output, _ = proc.communicate()

    return output


def checkImportErrors(outputFile):
    importList = ["socket", "struct", "subprocess"]
    failedImports = [x for x in importList if x not in sys.modules]

    if len(failedImports) == 0:
        return

    with open(outputFile, "w") as outputHandle:
        outputHandle.write("\n".join(failedImports))


def loadScene(scene):
    if not documents.LoadFile(scene):
        print("Failed to Load File: %s" % scene)
        return False
    return True


def runScript(script):
    if ntpath.isfile(script) is False:
        return False
    fl = open(script, 'rb')
    contents = fl.read()
    contents = contents.replace(b"\r", b"")
    code = compile(contents, script, 'exec')
    scope = {'__file__': script, '__name__': '__main__'}
    exec (code, scope)

    return True


def ParseCommandline(argv):
    for arg in argv:
        if arg.find("-DeadlineConnect") == 0:
            DeadlineConnect(arg)
            return True
    return False


def PluginMessage(id, data):
    if id == c4d.C4DPL_COMMANDLINEARGS:
        # Extend command line parsing, here
        # This is the last plugin message on Cinema 4D's start process
        return ParseCommandline(sys.argv)
    return False


def toBytes(val):
    if isinstance(val, unicode_type):
        return val.encode('utf-8')
    return val


def toStr(val):
    if not isinstance(val, unicode_type):
        return val.decode('utf-8')
    return val


def send_msg(sock, msg):
    # Prefix each message with a 4-byte length (network byte order)
    msg = struct.pack('>I', len(msg)) + toBytes(msg)
    sock.sendall(msg)


def recv_msg(sock):
    # Read message length and unpack it into an integer
    raw_msglen = recvall(sock, 4)
    if not raw_msglen:
        return None
    msglen = struct.unpack('>I', raw_msglen)[0]
    # Read the message data
    return toStr(recvall(sock, msglen))


def recvall(sock, n):
    # Helper function to recv n bytes or return None if EOF is hit
    data = b''
    try:
        while len(data) < n:
            try:
                packet = sock.recv(n - len(data))
                if not packet:
                    return None
                data += packet
            except socket.error as e:
                # If the socket receive is interrupted by the system retry the call.
                if e.errno != errno.EINTR:
                    raise
    except:
        print(traceback.format_exc())
    return data


# Pathmapping FUNCTIONS
indent = ""
indentStep = "    "


def Indent():
    global indent
    indent += indentStep


def RevertIndent():
    global indent
    indent = indent[:len(indent) - len(indentStep)]


def ResetIndent():
    global indent
    indent = ""


def GrabAllObjectsWithPaths(doc, args):
    # Walk all objects...
    ResetIndent()
    WalkTree(doc, doc.GetFirstObject(), pathmapFilenames, args)
    # ... and materials
    ResetIndent()
    WalkTree(doc, doc.GetFirstMaterial(), pathmapFilenames, args)


def setTextureSearchPaths(searchPaths):
    """
    Sets the global texture search paths based on the cinema4d major version
    :param searchPaths: a list of search paths
    :return: None
    """
    c4dMajorVersion = c4d.GetC4DVersion() / 1000

    if c4dMajorVersion >= 20:
        print('Setting texture paths to: %s' % str(searchPaths))
        c4d.SetGlobalTexturePaths([[path.encode('utf-8'), True] for path in searchPaths])
    else:
        for index, searchPath in enumerate(searchPaths):
            # The display of texture search paths is 1-indexed, but actually setting the value is 0-indexed.
            print('Setting texture path %s to "%s"' % (index + 1, searchPath))
            c4d.SetGlobalTexturePath(index, searchPath)


def runPathMapping(deadlineTemp, texPathFilename):
    doc = documents.GetActiveDocument()

    if texPathFilename:
        searchPaths = []
        with open(texPathFilename, mode="r", encoding="utf-8") as texPathFile:
            for line in texPathFile:
                searchPaths.append(line.strip())

        setTextureSearchPaths(searchPaths)

    # Grab all objects, and associated parameters, that contain file paths into a list of tuples
    objectsWithPaths = []
    GrabAllObjectsWithPaths(doc, objectsWithPaths)
    print("Collected %s filepath(s)" % len(objectsWithPaths))

    # Write all original paths to input file
    pathMapFilename = os.path.join(deadlineTemp, "pathMapFile.txt")
    with open(pathMapFilename, "w", encoding="utf-8") as pathMapFile:
        for obj, paramid in objectsWithPaths:
            path = toStr( obj[paramid].strip()) + u"\n"
            pathMapFile.write( path )

    # Apply pathmapping to each path and save in output file
    CallDeadlineCommand(["-CheckPathMappingInFile", pathMapFilename, pathMapFilename])

    # Read in pathmapped paths
    mappedPaths = []
    with open(pathMapFilename, "r", encoding="utf-8") as pathMapFile:
        for line in pathMapFile:
            mappedPaths.append(toStr(line.strip()))
    
    # Update the object attributes with the new paths
    for (obj, paramid), mappedPath in zip(objectsWithPaths, mappedPaths):
        print("Mapping path: %s -> %s" % (obj[paramid], mappedPath))
        if sys.version_info[0] == 2:
            mappedPath = toBytes( mappedPath)
        obj[paramid] = mappedPath

# Traverse the tree of any BaseList2D derived entity and
# call DoPerEntity() for every single one
def WalkTree(doc, bl2d, func, args):
    while bl2d:
        DoPerEntity(doc, bl2d, func, args)
        Indent()
        WalkTree(doc, bl2d.GetDown(), func, args)
        RevertIndent()
        bl2d = bl2d.GetNext()


# Calls func for an BaseList2D derived entity
# For objects it will also take care of attached tags and
# call DoPerEntity() again for each tag
def DoPerEntity(doc, bl2d, func, args):
    global isVerbose

    if bl2d is None:
        return

    for objType in [c4d.Obase, c4d.Mbase, c4d.Tbase, c4d.Xbase]:
        if bl2d.CheckType(objType):
            if isVerbose:
                print(indent + bl2d.GetTypeName() + ": " + bl2d.GetName())
            break
    else:
        return

    # Here we do, what ever needs to be done
    func(bl2d, args)
    # Handle shaders on objects (e.g. for deformers, modifiers,...), materials, tags or shaders
    WalkShaders(doc, bl2d, func, args)
    # Handle shaders on tags of object
    if not bl2d.CheckType(c4d.Obase):
        return
    Indent()
    tag = bl2d.GetFirstTag()
    while tag:
        DoPerEntity(doc, tag, func, args)
        tag = tag.GetNext()
    RevertIndent()


# Check the description of an entity (any BaseList2D derived instance)
# for shader links and call DoPerEntity() for every sub-shader
def WalkShaders(doc, bl2d, func, args):
    if bl2d is None:
        return
    Indent()
    # Unfortunately Layer shader needs special handling,
    #   as it doesn't store shader links in the description
    if bl2d.CheckType(c4d.Xlayer):
        subshd = bl2d.GetDown()
        while subshd:
            DoPerEntity(doc, subshd, func, args)
            subshd = subshd.GetNext()
    else:
        description = bl2d.GetDescription(c4d.DESCFLAGS_DESC_0)  # Get the description of the entity (BaseList2D)
        try:
            # In Cinema 4D R21 and later, this loop will raise a SystemError if called on hidden sub-objects
            # of certain generator objects, typically with names like "Cache Proxy Tag".
            # We check to see if the loop will raise this error before the real loop since we
            # do not want to catch any other SystemErrors that might be thrown by the body of the loop.
            for _bc, _paramid, _groupid in description:
                continue
        except SystemError:
            print('WARNING: Could not iterate over description. Skipping walking shader.')
            if isVerbose:
                print(traceback.format_exc())
        else:
            for bc, paramid, groupid in description:  # Iterate over the parameters of the description
                if (paramid.GetDepth() > 0) and (paramid[0].dtype == c4d.DTYPE_BASELISTLINK):
                    shd = bl2d[paramid]
                    try:
                        if shd is not None and shd.CheckType(c4d.Xbase):
                            DoPerEntity(doc, shd, func, args)
                    except:
                        print("Failed to walk Parameter: " + bc[c4d.DESC_NAME])

    RevertIndent()


# Check the description of an entity (any BaseList2D derived instance)
# for parameters of type DTYPE_FILENAME and print the parameter.
def pathmapFilenames(bl2d, pathmappings):
    global isVerbose

    if bl2d is None:
        return

    description = bl2d.GetDescription(c4d.DESCFLAGS_DESC_0)  # Get the description of the entity (BaseList2D)

    try:
        # In Cinema 4D R21 and later, this loop will raise a SystemError if called on hidden sub-objects
        # of certain generator objects, typically with names like "Cache Proxy Tag".
        # We check to see if the loop will raise this error before the real loop since we
        # do not want to catch any other SystemErrors that might be thrown by the body of the loop.
        for _bc, _paramid, _groupid in description:
            continue
    except SystemError:
        print('WARNING: Could not iterate over description. Skipping path mapping on object.')
        if isVerbose:
            print(traceback.format_exc())
    else:
        for bc, paramid, groupid in description:  # Iterate over the parameters of the description
            if paramid.GetDepth() > 0 and paramid[0].dtype == c4d.DTYPE_FILENAME:
                if bl2d[paramid] is not None and bl2d[paramid].strip() != "":
                    if isVerbose:
                        print(indent + indentStep + bc[c4d.DESC_NAME] + ": " + bl2d[paramid])
                    pathmappings.append((bl2d, paramid))
